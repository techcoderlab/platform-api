# ── Domain Layer: pure dataclasses, zero framework deps ──────────────────────
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AnalysisStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


@dataclass
class PageSnapshot:
    url:          str
    final_url:    str          # after redirects
    status_code:  int
    html:         str
    text:         str
    title:        str
    meta:         dict[str, str]
    links:        list[str]
    screenshots:  list[bytes] = field(default_factory=list)  # base64-ready bytes
    captured_at:  datetime    = field(default_factory=datetime.utcnow)


@dataclass
class AnalysisResult:
    job_id:      str
    url:         str
    status:      AnalysisStatus
    snapshot:    PageSnapshot | None        = None
    insights:    dict[str, Any]             = field(default_factory=dict)
    error:       str | None                 = None
    duration_ms: float                      = 0.0
    created_at:  datetime                   = field(default_factory=datetime.utcnow)


class BatchStatus(str, Enum):
    """Lifecycle states for a multi-page batch scraping job.

    PARTIAL is unique to batches — indicates some pages succeeded, some failed.
    """
    PENDING   = "pending"
    RUNNING   = "running"
    PARTIAL   = "partial"
    COMPLETED = "completed"
    FAILED    = "failed"


@dataclass
class BatchResult:
    """Aggregate result for a multi-page batch scraping job.

    Wraps N per-page AnalysisResult objects with batch-level metadata.
    The compiled_insights field contains merged data across all successful pages.

    Attributes:
        batch_id: Unique batch identifier for polling.
        urls: Original list of submitted URLs.
        status: Current batch lifecycle state.
        session_id: Shared browser session used across all pages.
        results: Per-page AnalysisResult objects (one per URL).
        compiled_insights: Merged insights across all successful pages.
        error: Batch-level error message (if entire batch failed).
        total_duration_ms: Wall-clock time for entire batch.
        created_at: Timestamp of batch submission.
    """
    batch_id:           str
    urls:               list[str]
    status:             BatchStatus
    session_id:         str
    results:            list[AnalysisResult]       = field(default_factory=list)
    compiled_insights:  dict[str, Any]             = field(default_factory=dict)
    error:              str | None                 = None
    total_duration_ms:  float                      = 0.0
    created_at:         datetime                   = field(default_factory=datetime.utcnow)