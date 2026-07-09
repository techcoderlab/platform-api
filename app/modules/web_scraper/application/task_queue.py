# ─────────────────────────────────────────────────────
# Module   : task_queue
# Layer    : Application
# Pillar   : P3 Concurrency (bounded queue, backpressure),
#            P4 Performance (non-blocking enqueue),
#            P6 Resilience (worker isolation)
# Complexity: enqueue O(1), worker_loop O(∞) amortized per-task O(1) dispatch
# ─────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger
log = get_logger(__name__)


class QueueFullError(Exception):
    """Raised when the bounded task queue rejects a new item (backpressure)."""


class TaskQueue:
    """Bounded async task queue with N concurrent workers.

    Provides backpressure via bounded asyncio.Queue (Pillar 3).
    Workers pull and execute coroutine functions concurrently.
    A worker never dies on task failure — errors are logged and the loop continues.

    Args:
        max_size: Maximum pending tasks before rejecting (backpressure).
        worker_count: Number of concurrent worker coroutines.
    """

    # Shared mutable state: _queue (asyncio.Queue — event-loop safe by design)
    # _workers list mutated only during start/stop lifecycle (no concurrent access).

    def __init__(self, max_size: int, worker_count: int) -> None:
        self._queue: asyncio.Queue[
            tuple[Callable[..., Awaitable[Any]], tuple[Any, ...], dict[str, Any]] | None
        ] = asyncio.Queue(maxsize=max_size)
        self._worker_count = worker_count
        self._workers: list[asyncio.Task[None]] = []

    @property
    def pending_count(self) -> int:
        """Current number of tasks waiting in the queue."""
        return self._queue.qsize()

    @property
    def is_full(self) -> bool:
        """Whether the queue has reached max capacity."""
        return self._queue.full()

    async def start(self) -> None:
        """Spawn worker coroutines. Idempotent — no-op if already started.

        Side effects:
            Creates self._worker_count asyncio.Tasks.
        """
        if self._workers:
            return
        for worker_id in range(self._worker_count):
            task = asyncio.create_task(
                self._worker_loop(worker_id),
                name=f"task-queue-worker-{worker_id}",
            )
            self._workers.append(task)
        log.info(
            "task_queue_started",
            extra={"worker_count": self._worker_count, "max_size": self._queue.maxsize},
        )

    async def stop(self) -> None:
        """Gracefully drain workers by sending sentinel None values.

        Waits for all in-flight tasks to complete before returning.

        Side effects:
            Cancels all worker asyncio.Tasks.
        """
        for _ in self._workers:
            await self._queue.put(None)
        results = await asyncio.gather(*self._workers, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                log.error(
                    "worker_shutdown_error",
                    extra={"worker_id": idx, "error_class": type(result).__name__, "error": str(result)},
                )
        self._workers.clear()
        log.info("task_queue_stopped")

    async def enqueue(
        self,
        coro_fn: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Non-blocking enqueue. Raises QueueFullError at capacity (Pillar 3).

        Args:
            coro_fn: Async callable to execute in a worker.
            *args: Positional arguments forwarded to coro_fn.
            **kwargs: Keyword arguments forwarded to coro_fn.

        Raises:
            QueueFullError: When bounded queue is full (backpressure signal).
        """
        try:
            self._queue.put_nowait((coro_fn, args, kwargs))
            log.debug("task_enqueued", extra={"pending": self._queue.qsize()})
        except asyncio.QueueFull:
            log.warning("task_queue_full", extra={"max_size": self._queue.maxsize})
            raise QueueFullError(
                f"Task queue at capacity ({self._queue.maxsize}). Try again later."
            )

    async def _worker_loop(self, worker_id: int) -> None:
        """Pull and execute tasks until a None sentinel is received.

        Workers are crash-isolated: a failing task logs an error
        but never kills the worker (Pillar 6 graceful degradation).

        Args:
            worker_id: Identifier for structured logging.
        """
        log.info("worker_started", extra={"worker_id": worker_id})
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                log.info("worker_stopped", extra={"worker_id": worker_id})
                return

            coro_fn, args, kwargs = item
            try:
                await coro_fn(*args, **kwargs)
            except Exception as exc:
                log.error(
                    "worker_task_failed",
                    extra={"worker_id": worker_id, "error_class": type(exc).__name__, "error": str(exc)},
                    exc_info=True,
                )
            finally:
                self._queue.task_done()
