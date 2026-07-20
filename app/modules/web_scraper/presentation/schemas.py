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


# ── Batch Request/Response DTOs ───────────────────────────────────────────────

class BatchAnalyzeRequest(BaseModel):
    """Inbound request to submit multiple URLs for batch analysis."""

    urls: list[HttpUrl] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="List of target URLs to scrape (max 5).",
        examples=[["https://example.com/page1", "https://example.com/page2"]],
    )
    wait_selector: str | None = Field(
        default=None,
        max_length=500,
        description="Optional CSS selector to wait for on each page before extracting content.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional shared session ID. Auto-generated if omitted.",
    )
    allow_mixed_domains: bool = Field(
        default=False,
        description="If false, enforces that all URLs belong to the same domain.",
    )
    page_delay_min: float = Field(
        default=1.0,
        ge=0.0,
        description="Minimum delay in seconds between page navigations.",
    )
    page_delay_max: float = Field(
        default=3.0,
        ge=0.0,
        description="Maximum delay in seconds between page navigations.",
    )

    @field_validator("wait_selector")
    @classmethod
    def sanitize_wait_selector(cls, v: str | None) -> str | None:
        """Reject selectors containing script injection vectors."""
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        forbidden = ("<", ">", "javascript:", "onclick", "onerror")
        lower = v.lower()
        for token in forbidden:
            if token in lower:
                raise ValueError(f"Selector contains forbidden token: {token!r}")
        return v

    from pydantic import model_validator
    @model_validator(mode="after")
    def validate_batch_rules(self) -> BatchAnalyzeRequest:
        # Validate delays
        if self.page_delay_min > self.page_delay_max:
            raise ValueError("page_delay_min cannot be greater than page_delay_max.")
            
        # Validate same domain if not allowed mixed
        if not self.allow_mixed_domains and len(self.urls) > 1:
            from urllib.parse import urlparse
            def _get_domain(url: HttpUrl) -> str:
                netloc = urlparse(str(url)).netloc.lower()
                return netloc[4:] if netloc.startswith("www.") else netloc
                
            first_domain = _get_domain(self.urls[0])
            for url in self.urls[1:]:
                domain = _get_domain(url)
                if domain != first_domain:
                    raise ValueError(
                        f"Mixed domains are not allowed unless allow_mixed_domains is true. "
                        f"Found {domain}, expected {first_domain}."
                    )
        return self


class BatchAnalyzeResponse(BaseModel):
    batch_id: str = Field(..., description="Unique batch job identifier for polling.")
    status: str = Field(..., description="Initial job status (always 'pending').")
    poll_url: str = Field(..., description="URL to poll for batch job status and results.")


class BatchPageResult(BaseModel):
    """Summarized result of a single page within a batch."""
    url: str
    status: str
    duration_ms: float
    job_id: str
    seo: dict[str, Any] = Field(default_factory=dict)
    content: dict[str, Any] = Field(default_factory=dict)
    leads: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class BatchSummary(BaseModel):
    total_pages: int
    successful_pages: int
    failed_pages: int
    # all_emails: list[str] = Field(default_factory=list)
    # all_phones: list[str] = Field(default_factory=list)
    # all_social_links: list[str] = Field(default_factory=list)


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    urls: list[str]
    session_id: str
    pages: list[BatchPageResult] = Field(default_factory=list)
    summary: BatchSummary | None = None
    error: str | None = None
    total_duration_ms: float = 0.0
    created_at: datetime


class BatchListResponse(BaseModel):
    count: int = Field(..., description="Number of results returned.")
    batches: list[BatchStatusResponse]
