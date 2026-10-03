# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.bootstrap
# Layer    : Composition root
# Pillar   : P1 Architecture (single place that wires ports to adapters),
#            P4 Performance (lazy load + idle unload on a 4 GB host)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger
from app.modules.wiskro.application.audio_quality import QualityThresholds
from app.modules.wiskro.application.inference_gate import InferenceGate
from app.modules.wiskro.application.render_cache import RenderCache
from app.modules.wiskro.application.stt_service import SpeechToTextService
from app.modules.wiskro.application.tts_service import TextToSpeechService
from app.modules.wiskro.domain.ports import ManagedModel
from app.modules.wiskro.infrastructure.audio_codec import PyAVAudioCodec
from app.modules.wiskro.infrastructure.kokoro_engine import KokoroOnnxEngine
from app.modules.wiskro.infrastructure.model_store import ModelStore, RemoteAsset
from app.modules.wiskro.infrastructure.whisper_engine import FasterWhisperEngine

log = get_logger(__name__)

_MAX_REAPER_INTERVAL_S = 60.0

# Quiet third-party telemetry/progress output in server logs
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


class WiskroRuntime:
    """Owns the wiskro services and the background lifecycle of their models."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        store = ModelStore(Path(settings.WISKRO_MODEL_DIR), allow_download=settings.WISKRO_ALLOW_DOWNLOAD)
        codec = PyAVAudioCodec(mp3_bitrate_kbps=settings.WISKRO_TTS_MP3_BITRATE_KBPS)

        stt_engine = FasterWhisperEngine(
            store=store,
            model=settings.WISKRO_STT_MODEL,
            compute_type=settings.WISKRO_STT_COMPUTE_TYPE,
            cpu_threads=settings.WISKRO_STT_CPU_THREADS,
            beam_size=settings.WISKRO_STT_BEAM_SIZE,
        )
        model_asset = RemoteAsset(settings.WISKRO_TTS_MODEL_URL, settings.WISKRO_TTS_MODEL_SHA256)
        tts_engine = KokoroOnnxEngine(
            store=store,
            model_asset=model_asset,
            voices_asset=RemoteAsset(settings.WISKRO_TTS_VOICES_URL, settings.WISKRO_TTS_VOICES_SHA256),
            cpu_threads=settings.WISKRO_TTS_CPU_THREADS,
            espeak_library=settings.WISKRO_ESPEAK_LIBRARY,
            espeak_data_path=settings.WISKRO_ESPEAK_DATA_PATH,
        )
        self._engines: tuple[ManagedModel, ...] = (stt_engine, tts_engine)
        self._gates = (
            self._gate("stt", settings.WISKRO_STT_MAX_CONCURRENCY),
            self._gate("tts", settings.WISKRO_TTS_MAX_CONCURRENCY),
        )

        self.stt = SpeechToTextService(
            engine=stt_engine,
            codec=codec,
            gate=self._gates[0],
            thresholds=QualityThresholds(
                min_duration_s=settings.WISKRO_STT_MIN_DURATION_SECONDS,
                min_speech_rms=settings.WISKRO_STT_MIN_SPEECH_RMS,
                min_snr_db=settings.WISKRO_STT_MIN_SNR_DB,
                min_speech_band_ratio=settings.WISKRO_STT_MIN_SPEECH_BAND_RATIO,
            ),
            max_duration_s=settings.WISKRO_STT_MAX_DURATION_SECONDS,
            default_language=settings.WISKRO_STT_DEFAULT_LANGUAGE,
        )
        self.tts = TextToSpeechService(
            engine=tts_engine,
            codec=codec,
            gate=self._gates[1],
            cache=RenderCache(max_bytes=settings.WISKRO_TTS_CACHE_MB * 1024 * 1024),
            default_voice=settings.WISKRO_TTS_DEFAULT_VOICE,
            model_id=f"kokoro/{model_asset.filename}",
        )
        self._tasks: list[asyncio.Task[None]] = []

    def _gate(self, name: str, max_concurrency: int) -> InferenceGate:
        return InferenceGate(
            name=name,
            max_concurrency=max_concurrency,
            max_pending=self._settings.WISKRO_MAX_PENDING,
            queue_timeout=self._settings.WISKRO_QUEUE_TIMEOUT_SECONDS,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Non-blocking: model downloads/loads run in the background so startup stays fast."""
        self._tasks.append(asyncio.create_task(self._warm_up(), name="wiskro-warm-up"))
        idle = self._settings.WISKRO_IDLE_UNLOAD_SECONDS
        if idle > 0:
            self._tasks.append(asyncio.create_task(self._reap_idle(idle), name="wiskro-idle-reaper"))
        log.info("wiskro_started", extra={"preload": self._settings.WISKRO_PRELOAD, "idle_unload_s": idle})

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for gate in self._gates:
            gate.shutdown()
        for engine in self._engines:
            engine.unload()
        log.info("wiskro_stopped")

    async def _warm_up(self) -> None:
        """Fetch model files ahead of the first request (and optionally load them)."""
        for engine in self._engines:
            try:
                await asyncio.to_thread(engine.ensure_assets)
                if self._settings.WISKRO_PRELOAD:
                    await asyncio.to_thread(engine.load)
            except Exception:
                # Degrade, don't crash: requests surface a 503 with the cause and retry the fetch
                log.exception("wiskro_warm_up_failed", extra={"model": engine.name})

    async def _reap_idle(self, idle_seconds: int) -> None:
        interval = min(_MAX_REAPER_INTERVAL_S, max(idle_seconds / 4, 1.0))
        while True:
            await asyncio.sleep(interval)
            for engine in self._engines:
                try:
                    await asyncio.to_thread(engine.unload_if_idle, idle_seconds)
                except Exception:
                    log.exception("wiskro_unload_failed", extra={"model": engine.name})
