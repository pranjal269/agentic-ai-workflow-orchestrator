"""The run store and the /workflow/runs API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.workflows.runs import RunRecord, RunStore


def _record(run_id: str, status: str = "completed") -> RunRecord:
    return RunRecord(
        id=run_id,
        workflow_id="workflow",
        goal="do a thing",
        status=status,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
        logs=[],
        results={"a": {"x": 1}},
    )


def test_store_keeps_newest_first_and_is_bounded():
    store = RunStore(capacity=2)
    store.add(_record("run_1"))
    store.add(_record("run_2"))
    store.add(_record("run_3"))

    ids = [summary.id for summary in store.list()]
    assert ids == ["run_3", "run_2"]  # run_1 evicted, newest first
    assert store.get("run_1") is None
    assert store.list()[0].node_count == 1


def test_execute_then_fetch_run_via_api():
    client = TestClient(app)
    workflow = {
        "trigger": {"type": "manual_trigger", "params": {}},
        "nodes": [{"id": "shape", "type": "set", "params": {"values": {"hi": "there"}}}],
        "edges": [{"source": "trigger", "target": "shape"}],
    }

    executed = client.post("/workflow/execute", json=workflow).json()
    assert executed["status"] == "completed"
    run_id = executed["run_id"]

    listing = client.get("/workflow/runs").json()
    assert any(item["id"] == run_id for item in listing)

    detail = client.get(f"/workflow/runs/{run_id}").json()
    assert detail["results"]["shape"]["values"] == {"hi": "there"}

    assert client.get("/workflow/runs/run_missing").status_code == 404
