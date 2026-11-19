"""Shared test fixtures and workflow builders.

The suite is hermetic: it must never reach the network or need an API key, even
when the developer has a real key in ``backend/.env``. Forcing the stub backends
here (before ``app.core.config`` is first imported) guarantees that every
``PlannerService`` / ``WorkflowExecutor`` built during a test run uses the
deterministic offline stubs.
"""

from __future__ import annotations

import os

os.environ["PLANNER_BACKEND"] = "stub"
os.environ["EXECUTOR_LLM_BACKEND"] = "stub"

from app.schemas.workflow import (
    BindingRef,
    TriggerConfig,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)


def linear_workflow() -> Workflow:
    """trigger -> fetch (http_request) -> summarize (llm_step)."""

    return Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="fetch", type="http_request", params={"url": "https://example.com"}),
            WorkflowNode(
                id="summarize",
                type="llm_step",
                params={"prompt": "Summarise {{ input.body }}"},
                bindings={"body": BindingRef(node="fetch", path="body")},
            ),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="fetch"),
            WorkflowEdge(source="fetch", target="summarize"),
        ],
    )


def branching_workflow() -> Workflow:
    """trigger -> check (if) --true--> yes (set) / --false--> no (set)."""

    return Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="check", type="if", params={"condition": "{{ trigger.startedAt }}"}),
            WorkflowNode(id="yes", type="set", params={"values": {"branch": "true"}}),
            WorkflowNode(id="no", type="set", params={"values": {"branch": "false"}}),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="check"),
            WorkflowEdge(source="check", target="yes", source_handle="true"),
            WorkflowEdge(source="check", target="no", source_handle="false"),
        ],
    )


def looping_workflow(max_iterations: int | None = 3) -> Workflow:
    """trigger -> work (set) -> gate (if) --true--> work (loop back) / --false--> done."""

    return Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(
                id="work",
                type="set",
                params={"values": {"step": "again"}},
                max_iterations=max_iterations,
            ),
            WorkflowNode(id="gate", type="if", params={"condition": "{{ work.values }}"}),
            WorkflowNode(id="done", type="set", params={"values": {"final": True}}),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="work"),
            WorkflowEdge(source="work", target="gate"),
            WorkflowEdge(source="gate", target="work", source_handle="true"),
            WorkflowEdge(source="gate", target="done", source_handle="false"),
        ],
    )
