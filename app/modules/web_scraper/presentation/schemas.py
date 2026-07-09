# ─────────────────────────────────────────────────────
# Module   : schemas
# Layer    : Presentation
# Pillar   : P2 Security (input validation at boundary),
#            P8 Code Quality (strict Pydantic v2 DTOs)
# Complexity: O(1) — Pydantic validation per field
# ─────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator

# DATA: PUBLIC — all schema fields are safe to log and expose via API.


# ── Request DTOs ──────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """Inbound request to submit a URL for analysis.

    Attributes:
        url: Target URL (must be a valid HTTP/HTTPS URL).
        wait_selector: Optional CSS selector to await before page extraction.
    """

    url: HttpUrl = Field(
        ...,
        description="Target URL to scrape and analyze.",
        examples=["https://example.com"],
    )
    wait_selector: str | None = Field(
        default=None,
        max_length=500,
        description="Optional CSS selector to wait for before extracting content.",
        examples=["#main-content", "div.loaded"],
    )
    # Use same ID for context reuse
    session_id: str | None = Field(default=None, description="Use same ID for context reuse")

    @field_validator("wait_selector")
    @classmethod
    def sanitize_wait_selector(cls, v: str | None) -> str | None:
        """Reject selectors containing script injection vectors."""
        if v is None:
            return v
        # Strip leading/trailing whitespace
        v = v.strip()
        if not v:
            return None
        # Block obvious injection patterns
        forbidden = ("<", ">", "javascript:", "onclick", "onerror")
        lower = v.lower()
        for token in forbidden:
            if token in lower:
                raise ValueError(f"Selector contains forbidden token: {token!r}")
        return v


# ── Response DTOs ─────────────────────────────────────────────────────────────

class AnalyzeResponse(BaseModel):
    """Returned immediately after job submission."""

    job_id: str = Field(..., description="Unique job identifier for polling.")
    status: str = Field(..., description="Initial job status (always 'pending').")
    poll_url: str = Field(..., description="URL to poll for job status and results.")


class PageSnapshotResponse(BaseModel):
    # """Serialized view of a scraped page snapshot (excludes raw HTML and bytes)."""
    """
    Lightweight snapshot summary. 
    Heavy data (content, meta) is shifted to the insights dictionary.
    """
    # url: str
    # final_url: str
    # status_code: int
    # title: str
    # meta: dict[str, str]
    # link_count: int
    # text_length: int
    # has_screenshot: bool
    # captured_at: datetime
    url: str
    final_url: str
    status_code: int
    captured_at: datetime


class JobStatusResponse(BaseModel):
    """Full job status response with optional snapshot and insights."""

    job_id: str
    url: str
    status: str
    snapshot: PageSnapshotResponse | None = None
    insights: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    created_at: datetime


class JobListResponse(BaseModel):
    """Paginated list of recent jobs."""

    count: int = Field(..., description="Number of results returned.")
    jobs: list[JobStatusResponse]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Service status: 'healthy' or 'degraded'.")
    service: str = Field(default="web-analyst")
    version: str = Field(default="0.1.0")


class ReadinessResponse(BaseModel):
    """Readiness probe response with dependency status."""

    status: str
    dependencies: dict[str, str] = Field(
        default_factory=dict,
        description="Map of dependency name to status string.",
    )


class ErrorResponse(BaseModel):
    """Standard error envelope returned on 4xx/5xx."""

    error: str = Field(..., description="Error type identifier.")
    message: str = Field(..., description="Human-readable error description.")
    detail: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured error details.",
    )
