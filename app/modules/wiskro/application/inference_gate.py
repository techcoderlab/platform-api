# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.inference_gate
# Layer    : Application
# Pillar   : P3 Concurrency (bounded slots + dedicated thread pool),
#            P6 Resilience (backpressure: fail fast instead of queueing unbounded work)
# Complexity: O(1) admission
# ─────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from typing import TypeVar

from app.core.errors import ServiceUnavailableError, TooManyRequestsError

T = TypeVar("T")


class InferenceGate:
    """Admission control for CPU-bound inference on a small machine.

    - At most `max_concurrency` jobs run at once, each on a dedicated worker thread
      (so ML work never starves the shared default executor or the event loop).
    - At most `max_pending` callers may wait for a slot; beyond that → 429.
    - A caller waiting longer than `queue_timeout` → 503.
    """

    # Shared mutable state: _waiting is only touched from the event loop thread.

    def __init__(self, name: str, max_concurrency: int, max_pending: int, queue_timeout: float) -> None:
        self._name = name
        self._max_pending = max_pending
        self._queue_timeout = queue_timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix=f"wiskro-{name}")
        self._waiting = 0

    async def run(self, fn: Callable[..., T], *args: object) -> T:
        if self._semaphore.locked() and self._waiting >= self._max_pending:
            raise TooManyRequestsError(f"{self._name.upper()} is at capacity; retry shortly.")

        self._waiting += 1
        try:
            async with asyncio.timeout(self._queue_timeout):
                await self._semaphore.acquire()
        except TimeoutError:
            raise ServiceUnavailableError(f"{self._name.upper()} queue wait exceeded {self._queue_timeout:g}s.") from None
        finally:
            self._waiting -= 1

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, functools.partial(fn, *args))
        finally:
            self._semaphore.release()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
