# ─────────────────────────────────────────────────────
# Module   : routes
# Layer    : Presentation
# Pillar   : P1 Architecture (presentation-only: HTTP in/out),
#            P2 Security (input validation via Pydantic),
#            P6 Resilience (typed error mapping),
#            P7 Observability (health endpoints)
# Complexity: O(1) per endpoint (delegates to Application layer)
# ─────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.modules.web_scraper.application.analysis_service import AnalysisService
from app.modules.web_scraper.application.task_queue import QueueFullError
from app.modules.web_scraper.domain.models import AnalysisResult, AnalysisStatus, BatchResult, BatchStatus
from app.modules.web_scraper.presentation.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    HealthResponse,
    JobListResponse,
    JobStatusResponse,
    PageSnapshotResponse,
    ReadinessResponse,
    BatchAnalyzeRequest,
    BatchAnalyzeResponse,
    BatchStatusResponse,
    BatchListResponse,
    BatchPageResult,
    BatchSummary,
)

from app.core.logging import get_logger
log = get_logger(__name__)

# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter()


# ── Helper: domain model -> response DTO ──────────────────────────────────────

def _to_job_response(result: AnalysisResult) -> JobStatusResponse:
    """Map domain AnalysisResult to presentation JobStatusResponse.

    Strips raw HTML and screenshot bytes from the response payload
    to keep responses lean (Pillar 4 payload optimization).

    Args:
        result: Domain AnalysisResult entity.

    Returns:
        Presentation-layer DTO safe for JSON serialization.
    """
    snapshot_dto: PageSnapshotResponse | None = None
    if result.snapshot is not None:
        s = result.snapshot
        snapshot_dto = PageSnapshotResponse(
            url=s.url,
            final_url=s.final_url,
            status_code=s.status_code,
            captured_at=s.captured_at,
            title=s.title,
            meta=s.meta,
            link_count=len(s.links),
            text_length=len(s.text),
            has_screenshot=len(s.screenshots) > 0,
        )

    return JobStatusResponse(
        job_id=result.job_id,
        url=result.url,
        status=result.status.value,
        snapshot=snapshot_dto,
        insights=result.insights,
        error=result.error,
        duration_ms=result.duration_ms,
        created_at=result.created_at,
    )


def _to_batch_response(result: BatchResult) -> BatchStatusResponse:
    """Map domain BatchResult to presentation BatchStatusResponse."""
    summary_dto = None
    if result.compiled_insights.get("summary"):
        s = result.compiled_insights["summary"]
        summary_dto = BatchSummary(
            total_pages=s["total_pages"],
            successful_pages=s["successful_pages"],
            failed_pages=s["failed_pages"],
            all_emails=s["all_emails"],
            all_phones=s["all_phones"],
            all_social_links=s["all_social_links"],
        )

    pages = []
    for page in result.compiled_insights.get("pages", []):
        pages.append(
            BatchPageResult(
                url=page["url"],
                status=page["status"],
                duration_ms=page["duration_ms"],
                job_id=page["job_id"],
                seo=page.get("seo", {}),
                content=page.get("content", {}),
                leads=page.get("leads", {}),
                pitch_hooks=page.get("pitch_hooks", {}),
                error=page.get("error"),
            )
        )

    return BatchStatusResponse(
        batch_id=result.batch_id,
        status=result.status.value,
        urls=result.urls,
        session_id=result.session_id,
        pages=pages,
        summary=summary_dto,
        error=result.error,
        total_duration_ms=result.total_duration_ms,
        created_at=result.created_at,
    )


# ── Dependency accessor ──────────────────────────────────────────────────────
# The AnalysisService instance is attached to app.state during lifespan.
# This avoids a module-level singleton (Pillar 5 stateless / horizontal scaling).

def _get_service(request: Request) -> AnalysisService:
    """Extract AnalysisService from app.state (set during lifespan).

    Args:
        request: FastAPI Request object.

    Returns:
        The singleton AnalysisService for this process.

    Raises:
        RuntimeError: If service not initialized.
    """
    service: AnalysisService | None = getattr(request.app.state, "analysis_service", None)
    if service is None:
        raise RuntimeError("AnalysisService not initialized — check app lifespan.")
    return service


# ── Analysis endpoints ────────────────────────────────────────────────────────

@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        429: {"model": ErrorResponse, "description": "Queue at capacity"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
    summary="Submit a URL for analysis",
    description="Enqueues a background scraping + analysis job. Returns a job_id for polling.",
)
async def submit_analysis(body: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    """Accept a URL, validate, enqueue for background analysis.

    Args:
        body: Validated AnalyzeRequest DTO.
        request: FastAPI request (carries app.state).

    Returns:
        AnalyzeResponse with job_id and poll URL.

    Raises:
        HTTPException 429: When task queue is full.
    """
    service = _get_service(request)

    try:
        job_id = await service.submit_analysis(
            url=str(body.url),
            wait_selector=body.wait_selector,
            session_id=body.session_id,
        )
    except QueueFullError as exc:
        # Pillar 6: map domain error -> HTTP status at presentation boundary
        log.warning("queue_full_rejection", extra={"url": str(body.url)})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    poll_url = f"/jobs/{job_id}"
    log.info("analysis_accepted", extra={"job_id": job_id, "url": str(body.url)})

    return AnalyzeResponse(
        job_id=job_id,
        status=AnalysisStatus.PENDING.value,
        poll_url=poll_url,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
    summary="Get analysis job status",
    description="Retrieve the current status and results of an analysis job.",
)
async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
    """Look up a single analysis job by ID.

    Args:
        job_id: Unique job identifier from submission.
        request: FastAPI request.

    Returns:
        Full job status with optional snapshot and insights.

    Raises:
        HTTPException 404: When job_id does not exist.
    """
    service = _get_service(request)
    result = await service.get_job(job_id)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id!r} not found.",
        )

    return _to_job_response(result)


@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="List recent analysis jobs",
    description="Returns the most recent analysis jobs, newest first.",
)
async def list_jobs(
    request: Request,
    limit: int = 50,
) -> JobListResponse:
    """Paginated listing of recent jobs.

    Args:
        request: FastAPI request.
        limit: Max results (capped at 100 server-side).

    Returns:
        JobListResponse with count and job list.
    """
    # Cap at 100 to prevent abuse (Pillar 2)
    capped_limit = min(limit, 100)
    service = _get_service(request)
    results = await service.list_jobs(limit=capped_limit)

    return JobListResponse(
        count=len(results),
        jobs=[_to_job_response(r) for r in results],
    )


# ── Batch Analysis endpoints ──────────────────────────────────────────────────

@router.post(
    "/analyze/batch",
    response_model=BatchAnalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        429: {"model": ErrorResponse, "description": "Queue at capacity"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
    summary="Submit multiple URLs for batch analysis",
    description="Enqueues a background batch scraping job for 1-5 same-site URLs.",
)
async def submit_batch_analysis(body: BatchAnalyzeRequest, request: Request) -> BatchAnalyzeResponse:
    """Accept a list of URLs, validate, enqueue for background batch analysis.

    Args:
        body: Validated BatchAnalyzeRequest DTO.
        request: FastAPI request (carries app.state).

    Returns:
        BatchAnalyzeResponse with batch_id and poll URL.

    Raises:
        HTTPException 429: When task queue is full.
    """
    service = _get_service(request)

    try:
        batch_id = await service.submit_batch(
            urls=[str(url) for url in body.urls],
            wait_selector=body.wait_selector,
            session_id=body.session_id,
            page_delay_min=body.page_delay_min,
            page_delay_max=body.page_delay_max,
        )
    except QueueFullError as exc:
        log.warning("batch_queue_full_rejection", extra={"urls": [str(u) for u in body.urls]})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    poll_url = f"/batches/{batch_id}"
    return BatchAnalyzeResponse(
        batch_id=batch_id,
        status=BatchStatus.PENDING.value,
        poll_url=poll_url,
    )


@router.get(
    "/batches/{batch_id}",
    response_model=BatchStatusResponse,
    responses={404: {"model": ErrorResponse, "description": "Batch not found"}},
    summary="Get batch analysis status",
    description="Retrieve the current status and results of a batch analysis job.",
)
async def get_batch_status(batch_id: str, request: Request) -> BatchStatusResponse:
    """Look up a single batch job by ID.

    Args:
        batch_id: Unique batch identifier from submission.
        request: FastAPI request.

    Returns:
        Full batch status with summarized page results.

    Raises:
        HTTPException 404: When batch_id does not exist.
    """
    service = _get_service(request)
    result = await service.get_batch(batch_id)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch {batch_id!r} not found.",
        )

    return _to_batch_response(result)


@router.get(
    "/batches",
    response_model=BatchListResponse,
    summary="List recent batch analysis jobs",
    description="Returns the most recent batch jobs, newest first.",
)
async def list_batches(
    request: Request,
    limit: int = 50,
) -> BatchListResponse:
    """Paginated listing of recent batches.

    Args:
        request: FastAPI request.
        limit: Max results (capped at 100 server-side).

    Returns:
        BatchListResponse with count and batch list.
    """
    capped_limit = min(limit, 100)
    service = _get_service(request)
    results = await service.list_batches(limit=capped_limit)

    return BatchListResponse(
        count=len(results),
        batches=[_to_batch_response(r) for r in results],
    )


# ── Health endpoints (Pillar 7) ───────────────────────────────────────────────

@router.get(
    "/healthz/live",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 if the process is alive (Kubernetes liveness).",
)
async def liveness() -> HealthResponse:
    """Liveness check — always 200 if process is running.

    Returns:
        HealthResponse with status 'healthy'.
    """
    return HealthResponse(status="healthy")


@router.get(
    "/healthz/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "Not ready"}},
    summary="Readiness probe",
    description="Returns 200 if all critical dependencies are reachable.",
)
async def readiness(request: Request) -> ReadinessResponse:
    """Readiness check — verifies critical dependencies.

    Checks that the browser pool and task queue are operational.

    Args:
        request: FastAPI request (carries app.state).

    Returns:
        ReadinessResponse with per-dependency status.

    Raises:
        HTTPException 503: When any critical dependency is unavailable.
    """
    deps: dict[str, str] = {}
    is_ready = True

    # Check browser pool
    browser_pool = getattr(request.app.state, "browser_pool", None)
    if browser_pool is not None and browser_pool._browser is not None:
        deps["browser_pool"] = "ok"
    else:
        deps["browser_pool"] = "unavailable"
        is_ready = False

    # Check task queue
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is not None and len(task_queue._workers) > 0:
        deps["task_queue"] = "ok"
    else:
        deps["task_queue"] = "unavailable"
        is_ready = False

    response = ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        dependencies=deps,
    )

    if not is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
        )

    return response
