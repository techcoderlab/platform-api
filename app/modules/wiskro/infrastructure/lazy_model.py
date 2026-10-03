# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.infrastructure.lazy_model
# Layer    : Infrastructure
# Pillar   : P3 Concurrency (thread-safe load/use/unload),
#            P4 Performance (RAM held only while a model is in use)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import ctypes
import gc
import sys
import threading
import time
from abc import abstractmethod
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Generic, TypeVar

from app.core.logging import get_logger
from app.modules.wiskro.domain.errors import EngineUnavailableError
from app.modules.wiskro.domain.ports import ManagedModel

log = get_logger(__name__)

M = TypeVar("M")


def release_memory() -> None:
    """Collect garbage and hand freed heap pages back to the OS (glibc only)."""
    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


class LazyModel(ManagedModel, Generic[M]):
    """Template for models that load on first use and unload after idling.

    Subclasses implement `_ensure_assets` (fetch files) and `_load` (build the
    in-memory model). A use-count guarantees a model is never unloaded mid-inference.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.RLock()
        self._model: M | None = None
        self._assets_ready = False
        self._active = 0
        self._last_used = time.monotonic()

    # ── Template hooks ────────────────────────────────────────────────────────

    @abstractmethod
    def _ensure_assets(self) -> None: ...

    @abstractmethod
    def _load(self) -> M: ...

    def _on_unload(self) -> None:
        """Drop subclass caches derived from the model."""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def ensure_assets(self) -> None:
        with self._lock:
            if self._assets_ready:
                return
            try:
                self._ensure_assets()
            except EngineUnavailableError:
                raise
            except Exception as exc:
                log.exception("wiskro_assets_failed", extra={"model": self.name})
                raise EngineUnavailableError(f"{self.name} model files are unavailable: {exc}") from exc
            self._assets_ready = True

    def load(self) -> None:
        with self._lock:
            self._get_or_load()

    def _get_or_load(self) -> M:
        if self._model is not None:
            return self._model
        self.ensure_assets()
        started = time.perf_counter()
        log.info("wiskro_model_loading", extra={"model": self.name})
        try:
            self._model = self._load()
        except EngineUnavailableError:
            raise
        except Exception as exc:
            log.exception("wiskro_model_load_failed", extra={"model": self.name})
            raise EngineUnavailableError(f"{self.name} model failed to load: {exc}") from exc
        self._last_used = time.monotonic()
        log.info("wiskro_model_loaded", extra={"model": self.name, "seconds": round(time.perf_counter() - started, 2)})
        return self._model

    @contextmanager
    def use(self) -> Iterator[M]:
        with self._lock:
            model = self._get_or_load()
            self._active += 1
        try:
            yield model
        finally:
            with self._lock:
                self._active -= 1
                self._last_used = time.monotonic()

    def unload_if_idle(self, idle_seconds: float) -> bool:
        with self._lock:
            if self._model is None or self._active or time.monotonic() - self._last_used < idle_seconds:
                return False
            self._drop()
        release_memory()
        log.info("wiskro_model_unloaded", extra={"model": self.name, "reason": "idle"})
        return True

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._drop()
        release_memory()

    def _drop(self) -> None:
        self._model = None
        self._on_unload()
