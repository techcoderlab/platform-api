# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.render_cache
# Layer    : Application
# Pillar   : P4 Performance (repeat phrases served without inference),
#            P9 Data (RAM-bounded, process-local, never persisted)
# Complexity: get/put O(1) amortised
# ─────────────────────────────────────────────────────
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Generic, TypeVar

V = TypeVar("V")

_MAX_ENTRY_SHARE = 4   # a single entry may use at most 1/4 of the budget


class RenderCache(Generic[V]):
    """Thread-safe LRU cache bounded by total payload bytes rather than entry count.

    Voice agents repeat a small set of phrases (greetings, fillers, error prompts);
    caching their rendered audio turns a multi-second CPU job into a dict lookup.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, tuple[V, int]] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> V | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def put(self, key: str, value: V, size: int) -> None:
        if self._max_bytes <= 0 or size > self._max_bytes // _MAX_ENTRY_SHARE:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size -= previous[1]
            self._entries[key] = (value, size)
            self._size += size
            while self._size > self._max_bytes:
                _, (_, evicted) = self._entries.popitem(last=False)
                self._size -= evicted

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size = 0
