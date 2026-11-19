"""Health check route used by clients and deployment probes."""
from fastapi import APIRouter
router = APIRouter(tags=["health"])

@router.get("/health")
def health_check() -> dict[str, str]:
    """Return a simple service health response."""

    return {"status": "ok", "service": "workflow-orchestrator-backend"}


