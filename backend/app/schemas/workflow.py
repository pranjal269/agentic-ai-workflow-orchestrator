"""Workflow IR: the contract shared by the planner, validator, and executor.

The IR describes a directed workflow *graph*. It deliberately supports
non-linear control flow:

* branching   - a node may have several outgoing edges, each leaving from a
                named ``source_handle`` (e.g. ``"true"`` / ``"false"``);
* fan-in      - a node may have several incoming edges;
* cycles      - loop-back edges are allowed, provided at least one node in the
                cycle sets ``max_iterations`` so execution is bounded.

Data flows separately from control: a node reads values from other nodes'
outputs through ``bindings`` rather than through the edges.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Reserved id used by edges/bindings to refer to the workflow trigger.
TRIGGER_NODE_ID = "trigger"


class UserGoal(BaseModel):
    """Natural language goal provided by the user."""

    goal: str = Field(..., min_length=1, description="The user's desired outcome.")


class TriggerConfig(BaseModel):
    """How a workflow run starts."""

    type: str = Field(..., description="Node type of a 'trigger' category node.")
    params: dict[str, Any] = Field(default_factory=dict)


class BindingRef(BaseModel):
    """A pointer into another node's output document.

    ``node`` is the source node id (or ``"trigger"``). ``path`` is a dotted /
    bracketed accessor evaluated against that node's output at run time, e.g.
    ``"body.items[0].name"``. An empty path means the whole output.
    """

    node: str
    path: str = ""


class WorkflowNode(BaseModel):
    """A single step in the workflow graph."""

    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[str, BindingRef] = Field(
        default_factory=dict,
        description="Named inputs pulled from upstream node outputs.",
    )
    max_iterations: int | None = Field(
        default=None,
        ge=1,
        description="Loop guard. At least one node in any cycle must set this.",
    )


class WorkflowEdge(BaseModel):
    """A directed control-flow connection between two nodes."""

    source: str
    target: str
    source_handle: str = Field(
        default="main",
        description="Which output branch of the source node this edge leaves from.",
    )
    condition: str | None = Field(
        default=None,
        description="Optional expression gating traversal of this edge.",
    )


class Workflow(BaseModel):
    """A complete workflow graph produced by the planner."""

    id: str = "workflow"
    goal: str | None = None
    trigger: TriggerConfig
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Validation report
# --------------------------------------------------------------------------- #


class ValidationIssueOut(BaseModel):
    """A single validation error or warning."""

    code: str
    message: str
    location: str | None = None


class ValidationReport(BaseModel):
    """Result of statically validating a workflow before execution."""

    ok: bool
    errors: list[ValidationIssueOut] = Field(default_factory=list)
    warnings: list[ValidationIssueOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Execution result (shape kept stable for the current frontend)
# --------------------------------------------------------------------------- #


class ExecutionLog(BaseModel):
    """Human-readable log entry produced during workflow execution."""

    node_id: str
    message: str


class ExecutionResponse(BaseModel):
    """Result returned after executing a workflow."""

    status: Literal["completed", "failed"]
    logs: list[ExecutionLog]
    results: dict[str, Any]
    run_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
