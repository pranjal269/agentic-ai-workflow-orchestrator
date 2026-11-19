"""Planner orchestration: clarification, validation-driven repair, fallback.

These tests drive the service with scripted backends - no network, no API key.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.planner.backends import (
    BackendTurn,
    StubPlannerBackend,
    build_planner_backend,
    fallback_workflow,
)
from app.planner.service import PlannerService
from app.schemas.planner import PlanRequest
from app.schemas.workflow import UserGoal
from app.workflows.validator import validate_workflow


# --- scripted backend --------------------------------------------------------- #


class _ScriptedSession:
    def __init__(self, turns: list[BackendTurn]) -> None:
        self._turns = turns
        self._i = 0
        self.repair_calls: list[list[str]] = []

    def _advance(self) -> BackendTurn:
        turn = self._turns[min(self._i, len(self._turns) - 1)]
        self._i += 1
        return turn

    def first_turn(self) -> BackendTurn:
        return self._advance()

    def repair_turn(self, issues: list[str]) -> BackendTurn:
        self.repair_calls.append(issues)
        return self._advance()


class ScriptedBackend:
    name = "scripted"

    def __init__(self, turns: list[BackendTurn]) -> None:
        self._turns = turns
        self.last_session: _ScriptedSession | None = None

    def new_session(self, goal: str, clarifications: list) -> _ScriptedSession:
        self.last_session = _ScriptedSession(self._turns)
        return self.last_session


def _service(turns: list[BackendTurn], max_repairs: int = 2) -> tuple[PlannerService, ScriptedBackend]:
    backend = ScriptedBackend(turns)
    settings = Settings(PLANNER_MAX_REPAIR_ATTEMPTS=max_repairs)
    return PlannerService(backend=backend, settings=settings), backend


_VALID = {
    "trigger": {"type": "manual_trigger", "params": {}},
    "nodes": [
        {"id": "note", "type": "set", "params": {"values": {"ok": True}}, "bindings": {}, "max_iterations": None},
    ],
    "edges": [{"source": "trigger", "target": "note", "source_handle": "main", "condition": None}],
}

_INVALID = {
    "trigger": {"type": "manual_trigger", "params": {}},
    "nodes": [
        {"id": "call", "type": "http_request", "params": {"method": "GET"}, "bindings": {}, "max_iterations": None},
    ],
    "edges": [{"source": "trigger", "target": "call", "source_handle": "main", "condition": None}],
}


# --- tests ------------------------------------------------------------------ #


def test_plan_surfaces_clarification_questions():
    service, _ = _service([BackendTurn.clarify([{"id": "q1", "question": "Which channel?", "why": "needed"}])])
    result = service.plan(PlanRequest(goal="post a message"))

    assert result.status == "needs_input"
    assert [q.question for q in result.questions] == ["Which channel?"]
    assert result.workflow is None


def test_plan_returns_valid_workflow_first_try():
    service, backend = _service([BackendTurn.workflow(_VALID, assumptions=["assumed X"])])
    result = service.plan(PlanRequest(goal="save a note"))

    assert result.status == "planned"
    assert result.attempts == 1
    assert result.validation.ok
    assert "assumed X" in result.notes
    assert backend.last_session.repair_calls == []


def test_plan_repairs_an_invalid_workflow():
    service, backend = _service([BackendTurn.workflow(_INVALID), BackendTurn.workflow(_VALID)])
    result = service.plan(PlanRequest(goal="call an API"))

    assert result.status == "planned"
    assert result.attempts == 2
    assert result.validation.ok
    # the backend was told what was wrong
    assert any("missing_required_param" in issue for issue in backend.last_session.repair_calls[0])


def test_plan_gives_up_after_max_repairs_but_still_returns_workflow():
    service, backend = _service([BackendTurn.workflow(_INVALID)], max_repairs=2)
    result = service.plan(PlanRequest(goal="call an API"))

    assert result.status == "planned"
    assert result.attempts == 3  # first try + 2 repairs
    assert result.validation.ok is False
    assert result.workflow is not None
    assert len(backend.last_session.repair_calls) == 2
    assert any("unresolved validation errors" in note for note in result.notes)


def test_plan_recovers_from_unparseable_payload():
    service, _ = _service([BackendTurn.workflow({"nonsense": True}), BackendTurn.workflow(_VALID)])
    result = service.plan(PlanRequest(goal="do a thing"))

    assert result.status == "planned"
    assert result.validation.ok
    assert result.attempts == 2


def test_generate_falls_back_when_planner_asks_for_clarification():
    service, _ = _service([BackendTurn.clarify([{"id": "q1", "question": "Which one?"}])])
    workflow = service.generate(UserGoal(goal="something ambiguous"))

    assert validate_workflow(workflow).ok


def test_plan_result_is_json_serializable():
    service, _ = _service([BackendTurn.workflow(_VALID)])
    result = service.plan(PlanRequest(goal="save a note"))
    assert result.model_dump()["status"] == "planned"


# --- backend selection ----------------------------------------------------- #


def test_build_backend_is_stub_without_api_key():
    backend = build_planner_backend(Settings(PLANNER_BACKEND="auto", NVIDIA_API_KEY=None))
    assert isinstance(backend, StubPlannerBackend)


def test_build_backend_forced_stub():
    backend = build_planner_backend(Settings(PLANNER_BACKEND="stub", NVIDIA_API_KEY="nvapi-xxx"))
    assert isinstance(backend, StubPlannerBackend)


def test_build_backend_is_nvidia_with_api_key():
    from app.planner.backends import NvidiaPlannerBackend

    backend = build_planner_backend(Settings(PLANNER_BACKEND="auto", NVIDIA_API_KEY="nvapi-xxx"))
    assert isinstance(backend, NvidiaPlannerBackend)
    assert backend.name == "nvidia"


def test_stub_backend_first_turn_is_a_valid_workflow():
    turn = StubPlannerBackend().new_session("research something", []).first_turn()
    assert turn.kind == "workflow"
    workflow, _ = _coerce(turn.workflow_input)
    assert validate_workflow(workflow).ok


def test_fallback_workflow_is_valid_and_carries_goal():
    workflow = fallback_workflow("  my goal  ")
    assert workflow.goal == "my goal"
    assert validate_workflow(workflow).ok


def _coerce(raw):
    from app.planner.service import _coerce_workflow

    return _coerce_workflow(raw)


def test_plan_request_rejects_empty_goal():
    with pytest.raises(ValueError):
        PlanRequest(goal="")
