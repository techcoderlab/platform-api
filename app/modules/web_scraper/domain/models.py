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