"""Validator behaviour: accept sound graphs, reject the specific ways a
planner tends to get them wrong."""

from __future__ import annotations

from app.planner.service import PlannerService
from app.schemas.workflow import (
    BindingRef,
    TriggerConfig,
    UserGoal,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from app.workflows.validator import validate_workflow

from tests.conftest import branching_workflow, linear_workflow, looping_workflow


def _codes(issues):
    return {issue.code for issue in issues}


# --- graphs that must pass -------------------------------------------------- #


def test_linear_workflow_is_valid():
    result = validate_workflow(linear_workflow())
    assert result.ok, result.errors


def test_branching_workflow_is_valid():
    result = validate_workflow(branching_workflow())
    assert result.ok, result.errors


def test_loop_with_iteration_guard_is_valid():
    result = validate_workflow(looping_workflow(max_iterations=5))
    assert result.ok, result.errors


def test_placeholder_planner_output_is_valid():
    workflow = PlannerService().generate(UserGoal(goal="Research AI agents"))
    result = validate_workflow(workflow)
    assert result.ok, result.errors


# --- graphs that must fail ------------------------------------------------- #


def test_loop_without_guard_is_rejected():
    result = validate_workflow(looping_workflow(max_iterations=None))
    assert not result.ok
    assert "unbounded_cycle" in _codes(result.errors)


def test_unknown_node_type_is_rejected():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[WorkflowNode(id="a", type="teleport")],
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    assert "unknown_node_type" in _codes(validate_workflow(workflow).errors)


def test_missing_required_param_is_rejected():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[WorkflowNode(id="a", type="http_request", params={"method": "GET"})],
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    assert "missing_required_param" in _codes(validate_workflow(workflow).errors)


def test_required_param_satisfied_by_binding_is_ok():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(
                id="a",
                type="http_request",
                bindings={"url": BindingRef(node="trigger", path="startedAt")},
            )
        ],
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    assert "missing_required_param" not in _codes(validate_workflow(workflow).errors)


def test_bad_enum_value_is_rejected():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[WorkflowNode(id="a", type="http_request", params={"url": "x", "method": "FETCH"})],
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    assert "param_not_in_enum" in _codes(validate_workflow(workflow).errors)


def test_unknown_source_handle_is_rejected():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="c", type="if", params={"condition": "{{ x }}"}),
            WorkflowNode(id="d", type="set", params={"values": {}}),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="c"),
            WorkflowEdge(source="c", target="d", source_handle="maybe"),
        ],
    )
    assert "unknown_source_handle" in _codes(validate_workflow(workflow).errors)


def test_dangling_binding_is_rejected():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(
                id="a",
                type="llm_step",
                params={"prompt": "hi"},
                bindings={"ctx": BindingRef(node="ghost", path="body")},
            )
        ],
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    assert "binding_unknown_source" in _codes(validate_workflow(workflow).errors)


def test_edge_to_missing_node_is_rejected():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[WorkflowNode(id="a", type="set", params={"values": {}})],
        edges=[WorkflowEdge(source="trigger", target="nope")],
    )
    assert "edge_unknown_target" in _codes(validate_workflow(workflow).errors)


def test_unreachable_node_is_warned_not_errored():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="a", type="set", params={"values": {}}),
            WorkflowNode(id="orphan", type="set", params={"values": {}}),
        ],
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    result = validate_workflow(workflow)
    assert result.ok
    assert "unreachable_node" in _codes(result.warnings)


def test_non_trigger_type_as_trigger_is_rejected():
    workflow = Workflow(
        trigger=TriggerConfig(type="http_request"),
        nodes=[WorkflowNode(id="a", type="set", params={"values": {}})],
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    assert "not_a_trigger" in _codes(validate_workflow(workflow).errors)


def test_report_conversion_shape():
    report = validate_workflow(looping_workflow(max_iterations=None)).to_report()
    assert report.ok is False
    assert any(err.code == "unbounded_cycle" for err in report.errors)
