# ── Domain Ports: ABCs that Infrastructure must implement ────────────────────
from __future__ import annotations
from abc import ABC, abstractmethod
from app.modules.web_scraper.domain.models import PageSnapshot, AnalysisResult
from typing import Protocol, Dict, Any, Optional

class BrowserPort(ABC):
    """Contract for any browser backend (Playwright, Pydoll, etc.)."""

    @abstractmethod
    async def fetch(self, url: str, *, wait_selector: str | None = None, session_id: str | None = None) -> PageSnapshot:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class AnalysisRepository(ABC):
    """Persist / retrieve AnalysisResult (swap Redis, Postgres, in-mem)."""

    @abstractmethod
    async def save(self, result: AnalysisResult) -> None: ...

    @abstractmethod
    async def get(self, job_id: str) -> AnalysisResult | None: ...

    @abstractmethod
    async def list_recent(self, limit: int = 50) -> list[AnalysisResult]: ...
    
    

class SessionStorePort(Protocol):
    async def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Fetch saved cookies, proxy, and user-agent for a session."""
        ...

    async def save_state(self, session_id: str, state: Dict[str, Any], ttl_seconds: int = 300) -> None:
        """Save session state with an expiry time (TTL)."""
        ...