# ─────────────────────────────────────────────────────
# Module   : app.modules.health.routes
# ─────────────────────────────────────────────────────
from fastapi import APIRouter, Request
from app.core.responses import success_response
from app.core.config import settings

router = APIRouter(tags=["Health"])

@router.get("/healthz/live")
async def liveness(request: Request):
    """Kubernetes liveness probe - returns 200 if process is running"""
    return success_response(
        data={"status": "alive", "service": settings.SERVICE_NAME},
        request_id=getattr(request.state, "request_id", None)
    )

@router.get("/healthz/ready")
async def readiness(request: Request):
    """Kubernetes readiness probe - returns 200 if dependencies are reachable"""
    # For now, no external dependencies (DB/Redis) to check.
    return success_response(
        data={"status": "ready", "service": settings.SERVICE_NAME},
        request_id=getattr(request.state, "request_id", None)
    )
