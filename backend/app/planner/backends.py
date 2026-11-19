"""Planner backends.

A backend turns a goal into planner *turns*. Each request gets a fresh
``PlannerSession``:

    session = backend.new_session(goal, clarifications)
    turn = session.first_turn()          # clarify OR a workflow draft
    turn = session.repair_turn(issues)   # only if the draft failed validation

Two backends ship:

* :class:`NvidiaPlannerBackend` - real LLM planning against an OpenAI-compatible
  endpoint (NVIDIA's hosted API by default) via function calling, with the
  repair conversation kept on the session.
* :class:`StubPlannerBackend` - deterministic, no network; used when no API key
  is configured so the app and the test suite run offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.core.config import LLM_BACKEND_NAME, Settings, settings as default_settings
from app.core.llm_client import build_chat_client, thinking_extra_body
from app.planner import prompt as planner_prompt
from app.schemas.workflow import (
    BindingRef,
    TriggerConfig,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

_MAX_TOKENS = 8192


@dataclass
class BackendTurn:
    """One step from a backend: either questions or a workflow draft."""

    kind: Literal["clarify", "workflow"]
    questions: list[dict] = field(default_factory=list)
    workflow_input: Any = None
    assumptions: list[str] = field(default_factory=list)

    @classmethod
    def clarify(cls, questions: list[dict]) -> "BackendTurn":
        return cls(kind="clarify", questions=questions)

    @classmethod
    def workflow(cls, workflow_input: Any, assumptions: list[str] | None = None) -> "BackendTurn":
        return cls(kind="workflow", workflow_input=workflow_input, assumptions=assumptions or [])


class PlannerSession(Protocol):
    """A single planning conversation."""

    def first_turn(self) -> BackendTurn: ...

    def repair_turn(self, issues: list[str]) -> BackendTurn: ...


class PlannerBackend(Protocol):
    name: str

    def new_session(self, goal: str, clarifications: list) -> PlannerSession: ...


# --------------------------------------------------------------------------- #
# Stub backend
# --------------------------------------------------------------------------- #


def fallback_workflow(goal: str) -> Workflow:
    """A minimal, always-valid workflow.

    Used by the stub backend and as the last resort for ``/workflow/generate``
    so that endpoint always returns a runnable graph.

    Shape: trigger -> fetch (http_request) -> check (if)
                                                |-- true  --> summarize (llm_step)
                                                |-- false --> fallback (set)
    """

    clean_goal = goal.strip()
    nodes = [
        WorkflowNode(
            id="fetch",
            type="http_request",
            params={"url": "https://example.com/data.json", "method": "GET"},
        ),
        WorkflowNode(
            id="check",
            type="if",
            params={"condition": "{{ fetch.statusCode == 200 }}"},
            bindings={"status": BindingRef(node="fetch", path="statusCode")},
        ),
        WorkflowNode(
            id="summarize",
            type="llm_step",
            params={"prompt": f"Goal: {clean_goal}\n\nSummarise:\n{{{{ body }}}}"},
            bindings={"body": BindingRef(node="fetch", path="body")},
        ),
        WorkflowNode(
            id="fallback",
            type="set",
            params={"values": {"note": "Upstream request did not return 200."}},
        ),
    ]
    edges = [
        WorkflowEdge(source="trigger", target="fetch"),
        WorkflowEdge(source="fetch", target="check"),
        WorkflowEdge(source="check", target="summarize", source_handle="true"),
        WorkflowEdge(source="check", target="fallback", source_handle="false"),
    ]
    return Workflow(id="stub-workflow", goal=clean_goal, trigger=TriggerConfig(type="manual_trigger"), nodes=nodes, edges=edges)


class _StubSession:
    def __init__(self, goal: str) -> None:
        self._goal = goal

    def first_turn(self) -> BackendTurn:
        return BackendTurn.workflow(
            fallback_workflow(self._goal),
            assumptions=["Stub planner: fixed research-style workflow (no LLM configured)."],
        )

    def repair_turn(self, issues: list[str]) -> BackendTurn:  # pragma: no cover - stub is always valid
        return self.first_turn()


class StubPlannerBackend:
    """Deterministic planner used when no LLM is configured."""

    name = "stub"

    def new_session(self, goal: str, clarifications: list) -> PlannerSession:
        return _StubSession(goal)


# --------------------------------------------------------------------------- #
# OpenAI-compatible backend (NVIDIA)
# --------------------------------------------------------------------------- #


class _OpenAICompatSession:
    def __init__(
        self,
        client: Any,
        model: str,
        goal: str,
        clarifications: list,
        *,
        extra_body: dict | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._extra_body = extra_body
        self._last_tool_call_id: str | None = None
        self._messages: list[dict] = [
            {"role": "system", "content": planner_prompt.SYSTEM_PROMPT},
            {"role": "user", "content": planner_prompt.build_user_prompt(goal, clarifications)},
        ]

    def first_turn(self) -> BackendTurn:
        return self._call()

    def repair_turn(self, issues: list[str]) -> BackendTurn:
        feedback = planner_prompt.build_repair_prompt(issues)
        if self._last_tool_call_id is not None:
            self._messages.append(
                {"role": "tool", "tool_call_id": self._last_tool_call_id, "content": feedback}
            )
        else:
            self._messages.append({"role": "user", "content": feedback})
        return self._call()

    # -- transport ------------------------------------------------------- #

    def _complete(self) -> Any:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            "tools": planner_prompt.PLANNER_TOOLS,
            "temperature": 0,
            "max_tokens": _MAX_TOKENS,
        }
        if self._extra_body is not None:
            kwargs["extra_body"] = self._extra_body
        try:
            return self._client.chat.completions.create(tool_choice="required", **kwargs)
        except Exception:  # noqa: BLE001 - some OpenAI-compatible servers reject "required"
            try:
                return self._client.chat.completions.create(tool_choice="auto", **kwargs)
            except Exception as exc:  # noqa: BLE001
                raise PlannerError(f"planner LLM call failed: {exc}") from exc

    def _call(self) -> BackendTurn:
        message = self._complete().choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])

        assistant: dict[str, Any] = {"role": "assistant"}
        if getattr(message, "content", None):
            assistant["content"] = message.content
        if tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ]
        self._messages.append(assistant)

        if tool_calls:
            call = tool_calls[0]
            self._last_tool_call_id = call.id
            name = call.function.name
            payload = _loads(call.function.arguments)
        else:
            self._last_tool_call_id = None
            name = "submit_workflow"
            payload = _loads(getattr(message, "content", "") or "")

        if payload is None:
            raise PlannerError("Planner returned neither a usable tool call nor JSON.")
        if not isinstance(payload, dict):
            raise PlannerError("Planner returned a non-object payload.")

        if name == "request_clarification":
            return BackendTurn.clarify(payload.get("questions", []))

        assumptions = payload.pop("assumptions", [])
        return BackendTurn.workflow(payload, assumptions=assumptions)


class NvidiaPlannerBackend:
    """Real LLM planner backed by an OpenAI-compatible endpoint (NVIDIA)."""

    name = LLM_BACKEND_NAME

    def __init__(self, settings: Settings) -> None:
        self._client = build_chat_client(settings)
        self._model = settings.PLANNER_MODEL
        self._extra_body = thinking_extra_body(settings)

    def new_session(self, goal: str, clarifications: list) -> PlannerSession:
        return _OpenAICompatSession(
            self._client, self._model, goal, clarifications, extra_body=self._extra_body
        )


def _loads(raw: str) -> Any:
    """Parse JSON, tolerating markdown fences and surrounding prose."""

    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        return None


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


class PlannerError(RuntimeError):
    """Raised when the planner cannot be constructed or produces no usable turn."""


def build_planner_backend(settings: Settings | None = None) -> PlannerBackend:
    """Pick a backend from settings: the real LLM when a key is present, else ``stub``."""

    settings = settings or default_settings
    if settings.planner_backend_resolved == LLM_BACKEND_NAME:
        return NvidiaPlannerBackend(settings)
    return StubPlannerBackend()
