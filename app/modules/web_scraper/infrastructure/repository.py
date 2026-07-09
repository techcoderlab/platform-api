# ─────────────────────────────────────────────────────
# Module   : repository
# Layer    : Infrastructure
# Pillar   : P1 Repository pattern, P3 Concurrency (asyncio.Lock),
#            P4 Performance (O(1) lookup), P9 Data Management
# Complexity: save O(1), get O(1), list_recent O(n log n) due to sort
# ─────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy


from app.modules.web_scraper.domain.models import AnalysisResult
from app.modules.web_scraper.domain.ports import AnalysisRepository

from app.core.logging import get_logger
log = get_logger(__name__)

# ── Cache strategy (Pillar 4) ─────────────────────────────────────────────────
# Tier 1: In-process OrderedDict with LRU eviction.
# TTL      : N/A (in-memory, process-lifetime)
# Eviction : LRU when MAX_ENTRIES exceeded
# Invalidation: N/A for single-instance; swap to Redis adapter for multi-replica.
# TRADE-OFF: P5 Scalability — in-memory store is NOT horizontally scalable.
#   This implementation is intended for single-instance / dev / MVP usage.
#   Swap with a Redis or Postgres adapter for production multi-replica deployments.

MAX_ENTRIES = 10_000  # LRU cap to bound memory growth


class InMemoryAnalysisRepository(AnalysisRepository):
    """Thread-safe, LRU-bounded in-memory implementation of AnalysisRepository.

    Stores deep copies to prevent callers from mutating repository state.
    Protected by asyncio.Lock for single-event-loop safety (Pillar 3).

    Args:
        max_entries: Maximum results to retain before LRU eviction.
    """

    # Shared mutable state: _store (OrderedDict)
    # Protection strategy: asyncio.Lock — single event-loop, no cross-thread access.

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._store: OrderedDict[str, AnalysisResult] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_entries = max_entries

    async def save(self, result: AnalysisResult) -> None:
        """Persist or update an AnalysisResult by job_id.

        Args:
            result: The AnalysisResult to store (deep-copied).

        Side effects:
            Evicts oldest entry when capacity exceeded (LRU).
        """
        async with self._lock:
            # MUTATION: insert / update store entry
            self._store[result.job_id] = deepcopy(result)
            # Move to end (most-recently-used)
            self._store.move_to_end(result.job_id)
            # Evict LRU entries if over capacity — O(1) per eviction
            while len(self._store) > self._max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                log.debug("lru_eviction", extra={"evicted_job_id": evicted_key})

        log.debug("result_saved", extra={"job_id": result.job_id, "status": result.status.value})

    async def get(self, job_id: str) -> AnalysisResult | None:
        """Retrieve an AnalysisResult by job_id.

        Args:
            job_id: Unique job identifier.

        Returns:
            Deep copy of the stored result, or None if not found.
        """
        async with self._lock:
            result = self._store.get(job_id)
            if result is None:
                return None
            # Move to end on access (LRU touch)
            self._store.move_to_end(job_id)
            return deepcopy(result)

    # O(n log n) time due to sort, O(n) space for the slice
    async def list_recent(self, limit: int = 50) -> list[AnalysisResult]:
        """Return the most recent analysis results, ordered by created_at desc.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of deep-copied AnalysisResult, newest first.
        """
        async with self._lock:
            # Iterate in reverse insertion order (newest last in OrderedDict)
            items = list(self._store.values())

        # Sort by created_at descending — O(n log n)
        items.sort(key=lambda r: r.created_at, reverse=True)
        return [deepcopy(item) for item in items[:limit]]
