"""Static validation of a Workflow IR against the node catalog.

This runs before execution and is the system's main defence against a planner
(LLM or otherwise) producing something that cannot run: unknown node types,
mistyped or missing params, data bindings that point nowhere, edges leaving
from branches that do not exist, and - because the IR allows cycles on purpose
- cycles with no iteration guard.

Errors block execution; warnings do not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nodes.catalog import get_node_spec
from app.nodes.spec import NodeSpec
from app.schemas.workflow import (
    TRIGGER_NODE_ID,
    ValidationIssueOut,
    ValidationReport,
    Workflow,
    WorkflowNode,
)

_EXPR_MARKER = "{{"
_PATH_HEAD = re.compile(r"^([^.\[]+)")
_TRIGGER_SPEC_TYPE = "manual_trigger"  # any trigger spec; used only for output-field hints


@dataclass
class ValidationIssue:
    code: str
    message: str
    location: str | None = None


@dataclass
class ValidationResult:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str, location: str | None = None) -> None:
        self.errors.append(ValidationIssue(code, message, location))

    def warn(self, code: str, message: str, location: str | None = None) -> None:
        self.warnings.append(ValidationIssue(code, message, location))

    def to_report(self) -> ValidationReport:
        return ValidationReport(
            ok=self.ok,
            errors=[ValidationIssueOut(**vars(i)) for i in self.errors],
            warnings=[ValidationIssueOut(**vars(i)) for i in self.warnings],
        )


def validate_workflow(workflow: Workflow) -> ValidationResult:
    """Validate ``workflow`` and return the collected errors and warnings."""

    result = ValidationResult()

    node_ids = _check_node_ids(workflow, result)
    type_index = {node.id: node.type for node in workflow.nodes}

    _check_trigger(workflow, result)
    specs = _check_nodes(workflow, node_ids, type_index, result)
    _check_edges(workflow, node_ids, specs, result)
    _check_reachability(workflow, node_ids, result)
    _check_cycles(workflow, node_ids, result)

    return result


# --------------------------------------------------------------------------- #
# individual checks
# --------------------------------------------------------------------------- #


def _check_node_ids(workflow: Workflow, result: ValidationResult) -> set[str]:
    seen: set[str] = set()
    for node in workflow.nodes:
        if node.id == TRIGGER_NODE_ID:
            result.error(
                "reserved_node_id",
                f"'{TRIGGER_NODE_ID}' is reserved for the workflow trigger.",
                node.id,
            )
            continue
        if node.id in seen:
            result.error("duplicate_node_id", f"Node id '{node.id}' is used more than once.", node.id)
            continue
        seen.add(node.id)
    return seen


def _check_trigger(workflow: Workflow, result: ValidationResult) -> None:
    spec = get_node_spec(workflow.trigger.type)
    if spec is None:
        result.error(
            "unknown_trigger_type",
            f"No node type '{workflow.trigger.type}' in the catalog.",
            TRIGGER_NODE_ID,
        )
        return
    if spec.category != "trigger":
        result.error(
            "not_a_trigger",
            f"Node type '{spec.type}' has category '{spec.category}', not 'trigger'.",
            TRIGGER_NODE_ID,
        )
        return
    _check_params(spec, workflow.trigger.params, set(), TRIGGER_NODE_ID, result)


def _check_nodes(
    workflow: Workflow,
    node_ids: set[str],
    type_index: dict[str, str],
    result: ValidationResult,
) -> dict[str, NodeSpec]:
    specs: dict[str, NodeSpec] = {}
    for node in workflow.nodes:
        spec = get_node_spec(node.type)
        if spec is None:
            result.error("unknown_node_type", f"No node type '{node.type}' in the catalog.", node.id)
            continue
        if spec.category == "trigger":
            result.error(
                "trigger_used_as_node",
                f"Trigger type '{spec.type}' cannot be used as a workflow node.",
                node.id,
            )
            continue
        specs[node.id] = spec
        _check_params(spec, node.params, set(node.bindings), node.id, result)
        _check_bindings(node, node_ids, type_index, result)
    return specs


def _check_params(
    spec: NodeSpec,
    params: dict,
    binding_keys: set[str],
    location: str,
    result: ValidationResult,
) -> None:
    known = {p.name for p in spec.params}
    for key in params:
        if key not in known:
            result.warn("unknown_param", f"'{spec.type}' has no param '{key}'.", location)

    for param in spec.params:
        supplied = param.name in params or param.name in binding_keys
        if param.required and not supplied:
            result.error(
                "missing_required_param",
                f"'{spec.type}' requires param '{param.name}'.",
                location,
            )
            continue
        if param.name not in params:
            continue

        value = params[param.name]
        if isinstance(value, str) and _EXPR_MARKER in value:
            continue  # expression, resolved at run time
        if param.enum is not None and value not in param.enum:
            result.error(
                "param_not_in_enum",
                f"'{param.name}' must be one of {param.enum}; got {value!r}.",
                location,
            )
        if not _type_ok(value, param.type):
            result.error(
                "param_type_mismatch",
                f"'{param.name}' expects {param.type}; got {type(value).__name__}.",
                location,
            )


def _check_bindings(
    node: WorkflowNode,
    node_ids: set[str],
    type_index: dict[str, str],
    result: ValidationResult,
) -> None:
    for name, ref in node.bindings.items():
        if ref.node == node.id:
            result.error("binding_self_reference", f"Binding '{name}' points at its own node.", node.id)
            continue
        if ref.node != TRIGGER_NODE_ID and ref.node not in node_ids:
            result.error(
                "binding_unknown_source",
                f"Binding '{name}' points at unknown node '{ref.node}'.",
                node.id,
            )
            continue
        if not ref.path:
            continue

        source_type = _TRIGGER_SPEC_TYPE if ref.node == TRIGGER_NODE_ID else type_index.get(ref.node, "")
        source_spec = get_node_spec(source_type)
        if source_spec is None or not source_spec.outputs:
            continue

        head_match = _PATH_HEAD.match(ref.path)
        head = head_match.group(1) if head_match else ""
        if head and head not in source_spec.output_names():
            result.warn(
                "binding_unknown_output_field",
                f"Binding '{name}' reads '{head}', not a declared output of '{source_spec.type}'.",
                node.id,
            )


def _check_edges(
    workflow: Workflow,
    node_ids: set[str],
    specs: dict[str, NodeSpec],
    result: ValidationResult,
) -> None:
    for edge in workflow.edges:
        loc = f"edge:{edge.source}->{edge.target}"

        if edge.source != TRIGGER_NODE_ID and edge.source not in node_ids:
            result.error("edge_unknown_source", f"Edge source '{edge.source}' is not a node.", loc)
        if edge.target == TRIGGER_NODE_ID or edge.target not in node_ids:
            result.error("edge_unknown_target", f"Edge target '{edge.target}' is not a node.", loc)

        if edge.source == TRIGGER_NODE_ID:
            if edge.source_handle != "main":
                result.error("unknown_source_handle", f"Trigger has no handle '{edge.source_handle}'.", loc)
            continue

        source_spec = specs.get(edge.source)
        if source_spec and edge.source_handle not in source_spec.output_handles:
            result.error(
                "unknown_source_handle",
                f"'{source_spec.type}' has no handle '{edge.source_handle}'; "
                f"expected one of {source_spec.output_handles}.",
                loc,
            )


def _check_reachability(workflow: Workflow, node_ids: set[str], result: ValidationResult) -> None:
    if not node_ids:
        return

    adjacency: dict[str, list[str]] = {}
    for edge in workflow.edges:
        if edge.target in node_ids and (edge.source in node_ids or edge.source == TRIGGER_NODE_ID):
            adjacency.setdefault(edge.source, []).append(edge.target)

    if TRIGGER_NODE_ID not in adjacency:
        result.warn(
            "trigger_not_connected",
            "The trigger has no outgoing edge, so no node will run.",
            TRIGGER_NODE_ID,
        )

    visited: set[str] = set()
    queue: list[str] = [TRIGGER_NODE_ID]
    while queue:
        current = queue.pop()
        for nxt in adjacency.get(current, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)

    for node_id in sorted(node_ids - visited):
        result.warn("unreachable_node", f"Node '{node_id}' cannot be reached from the trigger.", node_id)


def find_cycles(workflow: Workflow) -> list[list[str]]:
    """Return each group of node ids that form a cycle (an SCC or a self-loop).

    Shared by the validator (to demand an iteration guard) and the executor
    (to bound how many times the whole cycle may repeat).
    """

    node_ids = {node.id for node in workflow.nodes}
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
    self_loops: set[str] = set()
    for edge in workflow.edges:
        if edge.source in node_ids and edge.target in node_ids:
            adjacency[edge.source].append(edge.target)
            if edge.source == edge.target:
                self_loops.add(edge.source)

    cycles: list[list[str]] = []
    for component in _strongly_connected(node_ids, adjacency):
        if len(component) > 1 or component[0] in self_loops:
            cycles.append(component)
    return cycles


def _check_cycles(workflow: Workflow, node_ids: set[str], result: ValidationResult) -> None:
    guarded = {node.id for node in workflow.nodes if node.max_iterations is not None}

    for component in find_cycles(workflow):
        if not (set(component) & guarded):
            result.error(
                "unbounded_cycle",
                "Cycle " + " -> ".join(component) + " has no iteration guard; "
                "set 'max_iterations' on one of its nodes.",
                ",".join(component),
            )


def _strongly_connected(
    node_ids: set[str], adjacency: dict[str, list[str]]
) -> list[list[str]]:
    """Tarjan's SCC algorithm. Graphs here are tiny, so recursion is fine."""

    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    components: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adjacency.get(v, []):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            components.append(component)

    for node_id in node_ids:
        if node_id not in index:
            strongconnect(node_id)
    return components


def _type_ok(value, param_type: str) -> bool:
    if param_type == "string":
        return isinstance(value, str)
    if param_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if param_type == "boolean":
        return isinstance(value, bool)
    if param_type == "object":
        return isinstance(value, dict)
    if param_type == "array":
        return isinstance(value, list)
    return True
