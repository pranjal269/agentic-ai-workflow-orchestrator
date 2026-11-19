"""The execution engine for the Workflow IR.

It validates the workflow, then walks the graph from the trigger and actually
runs each node (see :mod:`app.workflows.runners`): real HTTP requests, real LLM
calls, expression-evaluated ``set`` objects, and condition-routed ``if`` /
``loop`` control flow.

Key properties:

* **validation first** - an invalid workflow never runs;
* **data flow** - each node's ``bindings`` are resolved against upstream
  outputs and, together with every node output, form the expression context;
* **control flow by handle** - an edge is only followed when the source node
  makes its ``source_handle`` active (an ``if`` activates ``true`` *or*
  ``false``) and any edge ``condition`` evaluates truthy;
* **bounded** - ``max_iterations`` caps cycles and a hard step ceiling protects
  the process;
* **persisted** - every run is recorded in the :class:`RunStore`.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.config import Settings, settings as default_settings
from app.nodes.catalog import get_node_spec
from app.schemas.workflow import (
    TRIGGER_NODE_ID,
    ExecutionLog,
    ExecutionResponse,
    Workflow,
    WorkflowNode,
)
from app.workflows.expressions import ExpressionError, render, truthy
from app.workflows.runners import (
    HttpCaller,
    HttpxCaller,
    LlmCaller,
    NodeRunError,
    resolve_llm_caller,
    run_node,
)
from app.workflows.runs import RunRecord, RunStore, new_run_id, utc_now_iso
from app.workflows.validator import find_cycles, validate_workflow

# Hard ceiling so a mis-specified graph can never hang the process.
_MAX_STEPS = 500

_INDEX_SEGMENT = re.compile(r"\[(\d+)\]")


class WorkflowExecutor:
    """Validate, run, and record a workflow graph."""

    def __init__(
        self,
        *,
        http: HttpCaller | None = None,
        llm: LlmCaller | None = None,
        run_store: RunStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._http = http or HttpxCaller(
            timeout=self._settings.EXECUTOR_HTTP_TIMEOUT,
            max_bytes=self._settings.EXECUTOR_MAX_RESPONSE_BYTES,
        )
        self._llm = llm or resolve_llm_caller(self._settings)
        self._run_store = run_store

    def execute(self, workflow: Workflow) -> ExecutionResponse:
        started_at = utc_now_iso()
        validation = validate_workflow(workflow)
        if not validation.ok:
            logs = [
                ExecutionLog(
                    node_id=issue.location or "workflow",
                    message=f"{issue.code}: {issue.message}",
                )
                for issue in validation.errors
            ]
            return self._finish(workflow, "failed", logs, {}, started_at)

        nodes_by_id: dict[str, WorkflowNode] = {node.id: node for node in workflow.nodes}
        outgoing = _outgoing_edges(workflow)
        iteration_cap = _iteration_caps(workflow, nodes_by_id)

        logs: list[ExecutionLog] = []
        outputs: dict[str, Any] = {TRIGGER_NODE_ID: {"startedAt": started_at}}
        visits: dict[str, int] = {}

        frontier: list[str] = [edge.target for edge in outgoing.get(TRIGGER_NODE_ID, [])]
        steps = 0
        failed = False

        while frontier and steps < _MAX_STEPS and not failed:
            node_id = frontier.pop(0)
            node = nodes_by_id.get(node_id)
            if node is None:
                continue

            allowed = iteration_cap.get(node_id, node.max_iterations or 1)
            if visits.get(node_id, 0) >= allowed:
                logs.append(
                    ExecutionLog(node_id=node_id, message=f"Skipped: iteration limit ({allowed}) reached")
                )
                continue

            visits[node_id] = visits.get(node_id, 0) + 1
            steps += 1

            spec = get_node_spec(node.type)
            context = dict(outputs)
            context.update(_resolve_bindings(node, outputs))

            try:
                result = run_node(
                    node, spec, context, visits[node_id], http=self._http, llm=self._llm
                )
            except (NodeRunError, ExpressionError) as exc:
                logs.append(ExecutionLog(node_id=node_id, message=f"Error: {exc}"))
                failed = True
                break

            outputs[node_id] = result.output
            iteration_note = f" (iteration {visits[node_id]})" if allowed > 1 else ""
            logs.append(ExecutionLog(node_id=node_id, message=f"Ran {node.type}{iteration_note}"))

            active = set(result.handles)
            for edge in outgoing.get(node_id, []):
                if edge.source_handle not in active:
                    continue
                if edge.condition is not None:
                    try:
                        if not truthy(render(edge.condition, context)):
                            continue
                    except ExpressionError as exc:
                        logs.append(
                            ExecutionLog(node_id=node_id, message=f"Error in edge condition: {exc}")
                        )
                        failed = True
                        break
                logs.append(
                    ExecutionLog(
                        node_id=node_id,
                        message=f"-> {edge.target} via '{edge.source_handle}'",
                    )
                )
                frontier.append(edge.target)

        if failed:
            return self._finish(workflow, "failed", logs, outputs, started_at)
        if steps >= _MAX_STEPS:
            logs.append(
                ExecutionLog(node_id="workflow", message=f"Stopped: step ceiling ({_MAX_STEPS}) hit")
            )
            return self._finish(workflow, "failed", logs, outputs, started_at)

        return self._finish(workflow, "completed", logs, outputs, started_at)

    # ----------------------------------------------------------------------- #

    def _finish(
        self,
        workflow: Workflow,
        status: str,
        logs: list[ExecutionLog],
        results: dict[str, Any],
        started_at: str,
    ) -> ExecutionResponse:
        finished_at = utc_now_iso()
        run_id = new_run_id()
        if self._run_store is not None:
            self._run_store.add(
                RunRecord(
                    id=run_id,
                    workflow_id=workflow.id,
                    goal=workflow.goal,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    logs=logs,
                    results=results,
                )
            )
        return ExecutionResponse(
            status=status,
            logs=logs,
            results=results,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _outgoing_edges(workflow: Workflow) -> dict[str, list]:
    table: dict[str, list] = {}
    for edge in workflow.edges:
        table.setdefault(edge.source, []).append(edge)
    return table


def _iteration_caps(
    workflow: Workflow, nodes_by_id: dict[str, WorkflowNode]
) -> dict[str, int]:
    """Per-node visit ceiling.

    A node in a cycle is bounded by the whole cycle's budget - the largest
    ``max_iterations`` declared on any node of that cycle - so a loop's
    condition/gate node repeats as many times as its body, not just once.
    """

    caps: dict[str, int] = {}
    for component in find_cycles(workflow):
        budget = max(
            (nodes_by_id[nid].max_iterations or 1) for nid in component if nid in nodes_by_id
        )
        for nid in component:
            caps[nid] = budget
    return caps


def _resolve_bindings(node: WorkflowNode, outputs: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for name, ref in node.bindings.items():
        source = outputs.get(ref.node)
        resolved[name] = _walk_path(source, ref.path) if source is not None else None
    return resolved


def _walk_path(value: Any, path: str) -> Any:
    if not path:
        return value
    for raw_segment in path.split("."):
        segment, indices = _split_indices(raw_segment)
        if segment:
            if not isinstance(value, dict) or segment not in value:
                return None
            value = value[segment]
        for idx in indices:
            if not isinstance(value, list) or idx >= len(value):
                return None
            value = value[idx]
    return value


def _split_indices(segment: str) -> tuple[str, list[int]]:
    indices = [int(m.group(1)) for m in _INDEX_SEGMENT.finditer(segment)]
    name = _INDEX_SEGMENT.sub("", segment)
    return name, indices
