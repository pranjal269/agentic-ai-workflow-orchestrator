"""Run persistence.

Every call to the execution engine produces a :class:`RunRecord` - the goal,
the final status, timing, the per-node logs, and each node's output. Records
are kept in an in-memory :class:`RunStore` (newest first) so the API can expose
a run history and a run inspector without a database.

The store is deliberately a small, swappable seam: a later phase can drop in a
SQL- or file-backed implementation behind the same three methods.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.workflow import ExecutionLog


class RunRecord(BaseModel):
    """A single stored workflow run."""

    id: str
    workflow_id: str
    goal: str | None = None
    status: str
    started_at: str
    finished_at: str
    logs: list[ExecutionLog] = Field(default_factory=list)
    results: dict[str, Any] = Field(default_factory=dict)


class RunSummary(BaseModel):
    """The run-history view: everything but the per-node logs and outputs."""

    id: str
    workflow_id: str
    goal: str | None = None
    status: str
    started_at: str
    finished_at: str
    node_count: int

    @classmethod
    def of(cls, record: RunRecord) -> "RunSummary":
        return cls(
            id=record.id,
            workflow_id=record.workflow_id,
            goal=record.goal,
            status=record.status,
            started_at=record.started_at,
            finished_at=record.finished_at,
            node_count=len(record.results),
        )


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """Thread-safe, bounded, in-memory store of recent runs (newest first)."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._records: "OrderedDict[str, RunRecord]" = OrderedDict()
        self._lock = threading.Lock()

    def add(self, record: RunRecord) -> None:
        with self._lock:
            self._records[record.id] = record
            self._records.move_to_end(record.id, last=False)
            while len(self._records) > self._capacity:
                self._records.popitem(last=True)

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def list(self, limit: int = 50) -> list[RunSummary]:
        with self._lock:
            records = list(self._records.values())[:limit]
        return [RunSummary.of(record) for record in records]
