"""The OpenAI-compatible (NVIDIA) planner backend and llm_step caller.

Driven with a fake chat client - no network, no API key.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.planner.backends import NvidiaPlannerBackend, PlannerError
from app.workflows.runners import NvidiaLlmCaller, StubLlmCaller, resolve_llm_caller

_WORKFLOW_ARGS = {
    "trigger": {"type": "manual_trigger", "params": {}},
    "nodes": [{"id": "n", "type": "set", "params": {"values": {"a": 1}}, "bindings": {}, "max_iterations": None}],
    "edges": [{"source": "trigger", "target": "n", "source_handle": "main", "condition": None}],
    "assumptions": ["assumed a thing"],
}


# --- fake OpenAI client --------------------------------------------------- #


def _message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call(name, args, call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


class _Responder:
    """Callable that returns queued results (raising any that are exceptions)."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, BaseException):
            raise result
        return result


def _fake_client(responder):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=responder)))


def _backend_with(responder) -> NvidiaPlannerBackend:
    backend = NvidiaPlannerBackend(Settings(NVIDIA_API_KEY="nvapi-test"))
    backend._client = _fake_client(responder)
    return backend


# --- planner backend ---------------------------------------------------- #


def test_first_turn_parses_submit_workflow_tool_call():
    responder = _Responder(_response(_message(tool_calls=[_tool_call("submit_workflow", _WORKFLOW_ARGS)])))
    session = _backend_with(responder).new_session("do a thing", [])

    turn = session.first_turn()
    assert turn.kind == "workflow"
    assert turn.assumptions == ["assumed a thing"]
    assert "assumptions" not in turn.workflow_input  # popped off the payload
    assert turn.workflow_input["nodes"][0]["id"] == "n"
    assert responder.calls[0]["tool_choice"] == "required"


def test_first_turn_parses_request_clarification_tool_call():
    args = {"questions": [{"id": "q1", "question": "Which channel?"}]}
    responder = _Responder(_response(_message(tool_calls=[_tool_call("request_clarification", args)])))
    session = _backend_with(responder).new_session("post something", [])

    turn = session.first_turn()
    assert turn.kind == "clarify"
    assert turn.questions == args["questions"]


def test_falls_back_to_parsing_json_content_when_no_tool_call():
    responder = _Responder(_response(_message(content=json.dumps(_WORKFLOW_ARGS))))
    session = _backend_with(responder).new_session("do a thing", [])

    turn = session.first_turn()
    assert turn.kind == "workflow"
    assert turn.workflow_input["edges"][0]["target"] == "n"


def test_retries_with_tool_choice_auto_when_required_is_rejected():
    responder = _Responder(
        RuntimeError("tool_choice 'required' not supported"),
        _response(_message(tool_calls=[_tool_call("submit_workflow", _WORKFLOW_ARGS)])),
    )
    session = _backend_with(responder).new_session("do a thing", [])

    turn = session.first_turn()
    assert turn.kind == "workflow"
    assert [c["tool_choice"] for c in responder.calls] == ["required", "auto"]


def test_repair_turn_answers_the_prior_tool_call():
    responder = _Responder(
        _response(_message(tool_calls=[_tool_call("submit_workflow", _WORKFLOW_ARGS, "call_A")])),
        _response(_message(tool_calls=[_tool_call("submit_workflow", _WORKFLOW_ARGS, "call_B")])),
    )
    session = _backend_with(responder).new_session("do a thing", [])
    session.first_turn()
    session.repair_turn(["missing_required_param: 'url'"])

    sent = responder.calls[1]["messages"]
    tool_reply = next(m for m in sent if m.get("role") == "tool")
    assert tool_reply["tool_call_id"] == "call_A"
    assert "missing_required_param" in tool_reply["content"]


def test_unparseable_response_raises_planner_error():
    responder = _Responder(_response(_message(content="I cannot help with that.")))
    session = _backend_with(responder).new_session("do a thing", [])
    with pytest.raises(PlannerError):
        session.first_turn()


# --- llm_step caller -------------------------------------------------------- #


def test_resolve_llm_caller_is_stub_without_key():
    assert isinstance(resolve_llm_caller(Settings(NVIDIA_API_KEY=None)), StubLlmCaller)


def test_resolve_llm_caller_is_nvidia_with_key():
    assert isinstance(resolve_llm_caller(Settings(NVIDIA_API_KEY="nvapi-x")), NvidiaLlmCaller)


def test_nvidia_llm_caller_sends_system_and_returns_text():
    responder = _Responder(_response(_message(content="the answer")))
    caller = NvidiaLlmCaller(Settings(NVIDIA_API_KEY="nvapi-test"))
    caller._client = _fake_client(responder)

    out = caller("a prompt", system="be terse", model="nvidia/nemotron-3-super-120b-a12b", temperature=0)
    assert out == "the answer"
    sent = responder.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "be terse"}
    assert sent[1] == {"role": "user", "content": "a prompt"}
    assert responder.calls[0]["model"] == "nvidia/nemotron-3-super-120b-a12b"
