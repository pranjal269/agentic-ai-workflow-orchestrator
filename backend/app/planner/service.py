"""Planner orchestration.

Ties a backend to the validator: get a workflow draft, validate it, and on
failure feed the errors back to the backend for another attempt (up to
``PLANNER_MAX_REPAIR_ATTEMPTS``). The planner always returns its best effort -
if repairs run out, the workflow is still returned with ``validation.ok`` false
so the caller can see exactly what is wrong.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.core.config import Settings, settings as default_settings
from app.planner.backends import BackendTurn, PlannerBackend, build_planner_backend
from app.schemas.planner import ClarificationQuestion, PlanRequest, PlanResult
from app.schemas.workflow import UserGoal, Workflow
from app.workflows.validator import validate_workflow


class PlannerService:
    """Produce a validated workflow (or clarifying questions) from a goal."""

    def __init__(
        self,
        backend: PlannerBackend | None = None,
        settings: Settings | None = None,
    ) -> None:
        resolved = settings or default_settings
        self._backend = backend or build_planner_backend(resolved)
        self._max_repairs = max(0, resolved.PLANNER_MAX_REPAIR_ATTEMPTS)

    # -- primary API -------------------------------------------------------- #

    def plan(self, request: PlanRequest) -> PlanResult:
        session = self._backend.new_session(request.goal, request.clarifications)
        turn = session.first_turn()

        if turn.kind == "clarify":
            return PlanResult(
                status="needs_input",
                goal=request.goal,
                backend=self._backend.name,
                questions=[ClarificationQuestion(**q) for q in turn.questions],
            )

        workflow: Workflow | None = None
        report = None
        attempts = 0
        while True:
            attempts += 1
            workflow, parse_issues = _coerce_workflow(turn.workflow_input)

            if workflow is not None:
                report = validate_workflow(workflow).to_report()
                if report.ok or attempts > self._max_repairs:
                    break
                issues = [f"{e.code}: {e.message}" for e in report.errors]
            else:
                if attempts > self._max_repairs:
                    break
                issues = parse_issues

            turn = session.repair_turn(issues)
            if turn.kind == "clarify":
                return PlanResult(
                    status="needs_input",
                    goal=request.goal,
                    backend=self._backend.name,
                    questions=[ClarificationQuestion(**q) for q in turn.questions],
                    attempts=attempts,
                )

        notes = list(turn.assumptions)
        if report is not None and not report.ok:
            notes.append(f"Returned after {attempts} attempt(s) with unresolved validation errors.")

        return PlanResult(
            status="planned",
            goal=request.goal,
            backend=self._backend.name,
            workflow=workflow,
            validation=report,
            attempts=attempts,
            notes=notes,
        )

    # -- legacy shim ------------------------------------------------------- #

    def generate(self, user_goal: UserGoal) -> Workflow:
        """Back-compat entrypoint for ``POST /workflow/generate``.

        Always returns a Workflow: the planned one when available, otherwise a
        minimal valid fallback (e.g. the planner asked for clarification).
        """

        from app.planner.backends import fallback_workflow

        result = self.plan(PlanRequest(goal=user_goal.goal))
        if result.workflow is not None:
            return result.workflow
        return fallback_workflow(user_goal.goal)


def _coerce_workflow(raw: object) -> tuple[Workflow | None, list[str]]:
    """Turn a backend payload (dict or Workflow) into a Workflow, or collect errors."""

    if isinstance(raw, Workflow):
        return raw, []
    try:
        return Workflow.model_validate(raw), []
    except ValidationError as exc:
        return None, [_format_pydantic_error(err) for err in exc.errors()]


def _format_pydantic_error(err: dict) -> str:
    location = ".".join(str(part) for part in err.get("loc", ())) or "(root)"
    return f"schema error at {location}: {err.get('msg', 'invalid')}"
