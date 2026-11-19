"""Prompt and tool definitions for the LLM planner.

The planner is framed as a *compiler*: natural-language goal in, a Workflow IR
out, expressed by calling exactly one of two tools - ``submit_workflow`` when it
has enough to plan, or ``request_clarification`` when a required detail is
genuinely missing. The node catalog is injected as JSON so the model can only
reference node types that exist.

Tools are declared in OpenAI function-calling format; the planner talks to an
OpenAI-compatible endpoint (NVIDIA's hosted API by default).
"""

from __future__ import annotations

import json

from app.nodes.catalog import catalog_digest

_CATALOG_JSON = json.dumps(catalog_digest(), indent=2)

SYSTEM_PROMPT = f"""\
You are the planner in a workflow-automation platform. You translate a user's \
natural-language goal into a Workflow IR - a directed graph the platform's \
executor can run. You do not execute anything yourself.

Respond by calling exactly one tool. Never write prose. If for any reason you \
cannot make a tool call, reply with a single JSON object matching the \
`submit_workflow` arguments and nothing else.

## Workflow IR rules

- `trigger` starts the run and MUST be a node type whose category is "trigger".
- Every entry in `nodes` MUST use a `type` from the catalog below (never a
  trigger type). Give each node a short, unique, descriptive `id`.
- `edges` carry control flow. An edge leaves its source node from a named
  `source_handle`; use "main" unless the source declares other handles (e.g. an
  "if" node has "true" and "false"). The reserved id "trigger" is the source of
  the first edge(s).
- Data flows through `bindings`, not through edges: a binding names an input and
  points at another node's output as `{{"node": "<id or 'trigger'>", "path": "<dotted path>"}}`.
  Use `{{{{ expressions }}}}` inside string params to reference bound values.
- Branching (multiple edges out), fan-in (multiple edges in) and loops
  (edges that point back) are all allowed. Any loop MUST set `max_iterations`
  on at least one node in the cycle so it is bounded.
- Prefer the smallest graph that achieves the goal. Do not add nodes "just in
  case".

## When to ask for clarification

Call `request_clarification` only when a detail is REQUIRED to build a correct
workflow and cannot be reasonably assumed - for example a target channel or
spreadsheet, a schedule time, or which of several plausible data sources to use.
Otherwise make a sensible assumption, build the workflow, and record the
assumption in `assumptions`.

## Node catalog

{_CATALOG_JSON}

## Example

Goal: "When I trigger it, fetch https://api.example.com/status and, if it's up,
summarise the body."

submit_workflow({{
  "trigger": {{"type": "manual_trigger", "params": {{}}}},
  "nodes": [
    {{"id": "fetch", "type": "http_request",
     "params": {{"url": "https://api.example.com/status", "method": "GET"}},
     "bindings": {{}}, "max_iterations": null}},
    {{"id": "is_up", "type": "if",
     "params": {{"condition": "{{{{ fetch.statusCode == 200 }}}}"}},
     "bindings": {{"status": {{"node": "fetch", "path": "statusCode"}}}},
     "max_iterations": null}},
    {{"id": "summarise", "type": "llm_step",
     "params": {{"prompt": "Summarise this API response: {{{{ body }}}}"}},
     "bindings": {{"body": {{"node": "fetch", "path": "body"}}}},
     "max_iterations": null}}
  ],
  "edges": [
    {{"source": "trigger", "target": "fetch", "source_handle": "main", "condition": null}},
    {{"source": "fetch", "target": "is_up", "source_handle": "main", "condition": null}},
    {{"source": "is_up", "target": "summarise", "source_handle": "true", "condition": null}}
  ],
  "assumptions": []
}})
"""

_BINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "node": {"type": "string", "description": "Source node id, or 'trigger'."},
        "path": {"type": "string", "description": "Dotted/bracketed path into that node's output."},
    },
    "required": ["node", "path"],
}

_SUBMIT_WORKFLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "trigger": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["type", "params"],
        },
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    "params": {"type": "object"},
                    "bindings": {"type": "object", "additionalProperties": _BINDING_SCHEMA},
                    "max_iterations": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["id", "type", "params", "bindings", "max_iterations"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "source_handle": {"type": "string"},
                    "condition": {"type": ["string", "null"]},
                },
                "required": ["source", "target", "source_handle", "condition"],
            },
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Any assumptions made instead of asking the user.",
        },
    },
    "required": ["trigger", "nodes", "edges", "assumptions"],
}

_REQUEST_CLARIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["id", "question"],
            },
            "minItems": 1,
        }
    },
    "required": ["questions"],
}

SUBMIT_WORKFLOW_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_workflow",
        "description": "Submit a complete Workflow IR that satisfies the user's goal.",
        "parameters": _SUBMIT_WORKFLOW_SCHEMA,
    },
}

REQUEST_CLARIFICATION_TOOL = {
    "type": "function",
    "function": {
        "name": "request_clarification",
        "description": "Ask the user for details required before a correct workflow can be built.",
        "parameters": _REQUEST_CLARIFICATION_SCHEMA,
    },
}

PLANNER_TOOLS = [SUBMIT_WORKFLOW_TOOL, REQUEST_CLARIFICATION_TOOL]


def build_user_prompt(goal: str, clarifications: list) -> str:
    """Compose the first user turn from the goal and any answered questions."""

    lines = [f"Goal: {goal.strip()}"]
    if clarifications:
        lines.append("\nClarifications provided:")
        for item in clarifications:
            lines.append(f"- Q: {item.question}\n  A: {item.answer}")
    return "\n".join(lines)


def build_repair_prompt(issues: list[str]) -> str:
    """Compose the follow-up turn listing why the last workflow failed."""

    bullet_list = "\n".join(f"- {issue}" for issue in issues)
    return (
        "The workflow you submitted did not pass validation:\n"
        f"{bullet_list}\n\n"
        "Call submit_workflow again with these problems fixed. Keep everything "
        "that was already correct."
    )
