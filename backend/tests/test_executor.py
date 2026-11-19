"""The execution engine: real runners, handle routing, bounding, persistence."""

from __future__ import annotations

import pytest

from app.schemas.workflow import (
    BindingRef,
    TriggerConfig,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from app.workflows.executor import WorkflowExecutor
from app.workflows.runners import HttpReply, NodeRunError
from app.workflows.runs import RunStore

from tests.conftest import branching_workflow, linear_workflow, looping_workflow


class FakeHttp:
    """Records requests and replays queued (or a default) HttpReply."""

    def __init__(self, replies=None, error: Exception | None = None):
        self._replies = list(replies or [])
        self._error = error
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers, params, json_body):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "json": json_body}
        )
        if self._error is not None:
            raise self._error
        if self._replies:
            return self._replies.pop(0)
        return HttpReply(status_code=200, headers={"content-type": "application/json"}, body={"ok": True})


class FakeLlm:
    def __init__(self, text: str = "generated"):
        self.text = text
        self.calls: list[dict] = []

    def __call__(self, prompt, *, system, model, temperature):
        self.calls.append({"prompt": prompt, "system": system, "model": model, "temperature": temperature})
        return self.text


def _executor(http=None, llm=None, store=None) -> WorkflowExecutor:
    return WorkflowExecutor(
        http=http or FakeHttp(),
        llm=llm or FakeLlm(),
        run_store=store,
    )


def test_linear_run_calls_http_then_llm_with_bound_body():
    http = FakeHttp(
        replies=[HttpReply(200, {"content-type": "application/json"}, {"title": "hello"})]
    )
    llm = FakeLlm(text="a summary")
    response = _executor(http, llm).execute(linear_workflow())

    assert response.status == "completed"
    assert response.run_id and response.started_at and response.finished_at
    assert http.calls[0]["url"] == "https://example.com"
    assert response.results["fetch"]["body"] == {"title": "hello"}
    assert llm.calls[0]["prompt"].startswith("Summarise")
    assert response.results["summarize"]["text"] == "a summary"


def test_llm_prompt_interpolates_bound_values():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="fetch", type="http_request", params={"url": "https://x.test"}),
            WorkflowNode(
                id="summarize",
                type="llm_step",
                params={"prompt": "Title is {{ title }}", "system": "be terse"},
                bindings={"title": BindingRef(node="fetch", path="body.title")},
            ),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="fetch"),
            WorkflowEdge(source="fetch", target="summarize"),
        ],
    )
    http = FakeHttp(replies=[HttpReply(200, {"content-type": "application/json"}, {"title": "hello"})])
    llm = FakeLlm()
    _executor(http, llm).execute(workflow)
    assert llm.calls[0]["prompt"] == "Title is hello"
    assert llm.calls[0]["system"] == "be terse"


def test_if_activates_only_the_matching_branch():
    response = _executor().execute(branching_workflow())
    messages = " ".join(log.message for log in response.logs)
    # condition is "{{ trigger.startedAt }}" - a non-empty string, so truthy
    assert "via 'true'" in messages
    assert "via 'false'" not in messages
    assert "yes" in response.results
    assert "no" not in response.results


def test_if_false_branch():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="check", type="if", params={"condition": "{{ 1 == 2 }}"}),
            WorkflowNode(id="yes", type="set", params={"values": {"b": "t"}}),
            WorkflowNode(id="no", type="set", params={"values": {"b": "f"}}),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="check"),
            WorkflowEdge(source="check", target="yes", source_handle="true"),
            WorkflowEdge(source="check", target="no", source_handle="false"),
        ],
    )
    response = _executor().execute(workflow)
    assert "no" in response.results and "yes" not in response.results


def test_set_node_evaluates_expressions_against_bindings():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="fetch", type="http_request", params={"url": "https://x.test"}),
            WorkflowNode(
                id="shape",
                type="set",
                params={"values": {"code": "{{ status }}", "double": "{{ status * 2 }}"}},
                bindings={"status": BindingRef(node="fetch", path="body.n")},
            ),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="fetch"),
            WorkflowEdge(source="fetch", target="shape"),
        ],
    )
    http = FakeHttp(replies=[HttpReply(200, {"content-type": "application/json"}, {"n": 21})])
    response = _executor(http).execute(workflow)
    # a whole-template value keeps its evaluated type (int, not str)
    assert response.results["shape"]["values"] == {"code": 21, "double": 42}


def test_edge_condition_gates_traversal():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[
            WorkflowNode(id="fetch", type="http_request", params={"url": "https://x.test"}),
            WorkflowNode(id="after", type="set", params={"values": {"x": "1"}}),
        ],
        edges=[
            WorkflowEdge(source="trigger", target="fetch"),
            WorkflowEdge(
                source="fetch",
                target="after",
                condition="{{ fetch.statusCode == 200 }}",
            ),
        ],
    )
    http = FakeHttp(replies=[HttpReply(500, {}, "boom")])
    response = _executor(http).execute(workflow)
    assert response.status == "completed"
    assert "after" not in response.results  # 500 != 200, edge not followed


def test_loop_back_cycle_is_bounded_by_max_iterations():
    response = _executor().execute(looping_workflow(max_iterations=3))
    assert response.status == "completed"
    ran_work = [log for log in response.logs if log.node_id == "work" and log.message.startswith("Ran")]
    assert len(ran_work) == 3


def test_node_error_fails_run_and_keeps_partial_results():
    http = FakeHttp(error=NodeRunError("connection refused"))
    response = _executor(http).execute(linear_workflow())
    assert response.status == "failed"
    assert any("connection refused" in log.message for log in response.logs)
    assert "summarize" not in response.results


def test_invalid_workflow_fails_without_running():
    workflow = Workflow(
        trigger=TriggerConfig(type="manual_trigger"),
        nodes=[WorkflowNode(id="a", type="http_request", params={})],  # missing url
        edges=[WorkflowEdge(source="trigger", target="a")],
    )
    response = _executor().execute(workflow)
    assert response.status == "failed"
    assert response.results == {}
    assert any("missing_required_param" in log.message for log in response.logs)


def test_unguarded_cycle_is_refused():
    response = _executor().execute(looping_workflow(max_iterations=None))
    assert response.status == "failed"


def test_run_is_persisted_to_the_store():
    store = RunStore()
    response = _executor(store=store).execute(linear_workflow())
    record = store.get(response.run_id)
    assert record is not None
    assert record.status == "completed"
    assert "fetch" in record.results and "summarize" in record.results
    assert [s.id for s in store.list()] == [response.run_id]
