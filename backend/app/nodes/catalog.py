"""The initial node catalog.

Six node types are enough to express real non-linear workflows end to end:
a trigger, three general-purpose actions (HTTP, LLM, Set), and two control-flow
nodes (If, Loop). Service integrations (Slack, Gmail, Sheets, ...) are added in
later phases by appending :class:`NodeSpec` entries here - nothing else changes.
"""

from __future__ import annotations

from app.nodes.spec import NodeParam, NodeSpec, OutputField

_SPECS: list[NodeSpec] = [
    NodeSpec(
        type="manual_trigger",
        category="trigger",
        description="Starts a workflow run on demand, with no external event.",
        params=[],
        outputs=[
            OutputField(name="startedAt", type="string", description="ISO 8601 start time."),
        ],
        output_handles=["main"],
    ),
    NodeSpec(
        type="http_request",
        category="action",
        description="Send an HTTP request to a URL and return the response.",
        params=[
            NodeParam(name="url", type="string", required=True, description="Absolute request URL."),
            NodeParam(
                name="method",
                type="string",
                default="GET",
                enum=["GET", "POST", "PUT", "PATCH", "DELETE"],
                description="HTTP method.",
            ),
            NodeParam(name="headers", type="object", description="Request headers as a map."),
            NodeParam(name="query", type="object", description="Query string parameters as a map."),
            NodeParam(name="body", type="object", description="JSON request body."),
        ],
        outputs=[
            OutputField(name="statusCode", type="number"),
            OutputField(name="headers", type="object"),
            OutputField(name="body", type="object"),
        ],
        output_handles=["main"],
    ),
    NodeSpec(
        type="llm_step",
        category="action",
        description="Call an LLM with a prompt and return the generated text.",
        params=[
            NodeParam(name="prompt", type="string", required=True, description="User prompt; may contain {{ expressions }}."),
            NodeParam(name="system", type="string", description="Optional system instruction."),
            NodeParam(name="model", type="string", default="nvidia/nemotron-3-super-120b-a12b", description="OpenAI-compatible model id."),
            NodeParam(name="temperature", type="number", default=0, description="Sampling temperature."),
        ],
        outputs=[
            OutputField(name="text", type="string", description="Generated completion."),
        ],
        output_handles=["main"],
        credential_type="nvidia",
    ),
    NodeSpec(
        type="set",
        category="action",
        description="Build a static or derived object from literals and expressions.",
        params=[
            NodeParam(name="values", type="object", required=True, description="Map of output key to value or {{ expression }}."),
        ],
        outputs=[
            OutputField(name="values", type="object", description="The assembled object."),
        ],
        output_handles=["main"],
    ),
    NodeSpec(
        type="if",
        category="logic",
        description="Route control flow down the 'true' or 'false' branch based on a condition.",
        params=[
            NodeParam(name="condition", type="string", required=True, description="Expression evaluated as truthy/falsy."),
        ],
        outputs=[
            OutputField(name="result", type="boolean", description="The evaluated condition."),
        ],
        output_handles=["true", "false"],
    ),
    NodeSpec(
        type="loop",
        category="logic",
        description=(
            "Repeat the 'each' branch once per item (bind 'items' to an array) or "
            "while 'condition' holds. Emits on 'done' when finished."
        ),
        params=[
            NodeParam(name="items", type="array", description="Array to iterate over; usually a binding."),
            NodeParam(name="condition", type="string", description="While-expression; used when 'items' is not set."),
        ],
        outputs=[
            OutputField(name="item", type="object", description="Current item on the 'each' branch."),
            OutputField(name="index", type="number", description="Zero-based iteration counter."),
        ],
        output_handles=["each", "done"],
    ),
]

# type -> NodeSpec, the lookup the validator and executor use.
NODE_CATALOG: dict[str, NodeSpec] = {spec.type: spec for spec in _SPECS}


def get_node_spec(node_type: str) -> NodeSpec | None:
    """Return the spec for ``node_type``, or ``None`` if it is not registered."""

    return NODE_CATALOG.get(node_type)


def list_specs() -> list[NodeSpec]:
    """Return every registered node spec."""

    return list(NODE_CATALOG.values())


def catalog_digest() -> list[dict]:
    """Return a compact, JSON-friendly view of the catalog.

    This is the shape the LLM planner will be given as its available grammar in
    the next phase; exposing it now keeps the API and the planner in step.
    """

    return [
        {
            "type": spec.type,
            "category": spec.category,
            "description": spec.description,
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "required": p.required,
                    **({"enum": p.enum} if p.enum is not None else {}),
                    **({"default": p.default} if p.default is not None else {}),
                }
                for p in spec.params
            ],
            "outputs": [o.name for o in spec.outputs],
            "output_handles": spec.output_handles,
            **({"credential_type": spec.credential_type} if spec.credential_type else {}),
        }
        for spec in _SPECS
    ]
