"""Workflow catalog, planning, generation, validation, execution, and run routes."""

from fastapi import APIRouter, HTTPException

from app.nodes.catalog import catalog_digest
from app.planner.service import PlannerService
from app.schemas.planner import PlanRequest, PlanResult
from app.schemas.workflow import (
    ExecutionResponse,
    UserGoal,
    ValidationReport,
    Workflow,
)
from app.workflows.executor import WorkflowExecutor
from app.workflows.runs import RunRecord, RunStore, RunSummary
from app.workflows.validator import validate_workflow

router = APIRouter()
planner_service = PlannerService()
run_store = RunStore()
workflow_executor = WorkflowExecutor(run_store=run_store)


@router.get("/catalog")
def get_catalog() -> list[dict]:
    """Return the node catalog: every node type the planner may use."""

    return catalog_digest()


@router.post("/plan", response_model=PlanResult)
def plan_workflow(request: PlanRequest) -> PlanResult:
    """Plan a workflow from a goal.

    Returns either clarifying questions (`status="needs_input"`) or a validated
    workflow (`status="planned"`; check `validation.ok`).
    """

    return planner_service.plan(request)


@router.post("/generate", response_model=Workflow)
def generate_workflow(user_goal: UserGoal) -> Workflow:
    """Generate a workflow from a goal (no clarification loop; always returns a graph)."""

    return planner_service.generate(user_goal)


@router.post("/validate", response_model=ValidationReport)
def validate(workflow: Workflow) -> ValidationReport:
    """Statically validate a workflow against the node catalog."""

    return validate_workflow(workflow).to_report()


@router.post("/execute", response_model=ExecutionResponse)
def execute_workflow(workflow: Workflow) -> ExecutionResponse:
    """Validate, then run a workflow through the execution engine.

    The run is recorded; use its `run_id` with `GET /workflow/runs/{run_id}`.
    """

    return workflow_executor.execute(workflow)


@router.get("/runs", response_model=list[RunSummary])
def list_runs(limit: int = 50) -> list[RunSummary]:
    """Return recent workflow runs, newest first (summaries only)."""

    return run_store.list(limit=limit)


@router.get("/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str) -> RunRecord:
    """Return one recorded run in full, including per-node logs and outputs."""

    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No run '{run_id}'.")
    return record
