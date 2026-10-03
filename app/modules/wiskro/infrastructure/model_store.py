# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.infrastructure.model_store
# Layer    : Infrastructure
# Pillar   : P2 Security (SHA-256 pinned artifacts), P6 Resilience (retries,
#            atomic writes — a crash never leaves a half-written model behind)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.logging import get_logger
from app.modules.wiskro.domain.errors import EngineUnavailableError

log = get_logger(__name__)

_CHUNK_BYTES = 1 << 20
_TIMEOUT = httpx.Timeout(30.0, read=120.0)
_MIN_ASSET_BYTES = 1 << 20   # anything smaller is an error page, not a model


@dataclass(frozen=True)
class RemoteAsset:
    url: str
    sha256: str = ""   # empty = skip verification

    @property
    def filename(self) -> str:
        return self.url.rstrip("/").rsplit("/", 1)[-1]


class ModelStore:
    """Local, verified cache of downloadable model files under one root directory."""

    def __init__(self, root: Path, allow_download: bool) -> None:
        self.root = root
        self.allow_download = allow_download
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def directory(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure(self, asset: RemoteAsset, subdir: str) -> Path:
        """Return the local path of `asset`, downloading and verifying it if absent."""
        destination = self.directory(subdir) / asset.filename
        with self._lock_for(destination):
            if destination.is_file():
                if self._is_intact(destination, asset.sha256):
                    return destination
                log.warning("wiskro_asset_corrupt", extra={"path": str(destination)})
            if not self.allow_download:
                raise EngineUnavailableError(
                    f"Model file {destination} is missing or corrupt and WISKRO_ALLOW_DOWNLOAD is disabled."
                )
            self._download(asset, destination)
            return destination

    def _lock_for(self, path: Path) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(path, threading.Lock())

    @staticmethod
    def _is_intact(path: Path, sha256: str) -> bool:
        if path.stat().st_size < _MIN_ASSET_BYTES:
            return False
        if not sha256:
            return True
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
                digest.update(chunk)
        return digest.hexdigest() == sha256.lower()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, max=30),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def _download(self, asset: RemoteAsset, destination: Path) -> None:
        partial = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        log.info("wiskro_asset_downloading", extra={"url": asset.url, "path": str(destination)})
        try:
            with httpx.stream("GET", asset.url, follow_redirects=True, timeout=_TIMEOUT) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes(_CHUNK_BYTES):
                        handle.write(chunk)
                        digest.update(chunk)
            size = partial.stat().st_size
            if size < _MIN_ASSET_BYTES:
                raise EngineUnavailableError(f"Download of {asset.url} is too small ({size} bytes).")
            if asset.sha256 and digest.hexdigest() != asset.sha256.lower():
                raise EngineUnavailableError(f"Checksum mismatch for {asset.filename}; refusing to use it.")
            os.replace(partial, destination)
        except httpx.HTTPStatusError as exc:
            raise EngineUnavailableError(f"Download of {asset.url} failed: HTTP {exc.response.status_code}.") from exc
        finally:
            partial.unlink(missing_ok=True)
        log.info("wiskro_asset_ready", extra={"path": str(destination), "bytes": destination.stat().st_size})
