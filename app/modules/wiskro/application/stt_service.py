# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.stt_service
# Layer    : Application
# Pillar   : P4 Performance (zero disk I/O, noise rejected before inference),
#            P6 Resilience (explicit rejection reasons), P9 Data (audio never persisted)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import dataclasses
import time

from app.core.logging import get_logger
from app.modules.wiskro.application.audio_quality import QualityThresholds, analyze_audio
from app.modules.wiskro.application.inference_gate import InferenceGate
from app.modules.wiskro.application.transcript_filter import clean_segments, is_meaningful
from app.modules.wiskro.domain.models import (
    AUTO_LANGUAGE,
    AudioQuality,
    RejectionReason,
    TranscriptionOptions,
    TranscriptionResult,
)
from app.modules.wiskro.domain.ports import AudioCodecPort, SpeechToTextEngine

log = get_logger(__name__)


class SpeechToTextService:
    """Use case: audio bytes in → filtered transcript out.

    Pipeline: decode (RAM) → acoustic gate → VAD + Whisper → hallucination and
    repetition cleanup → meaningfulness filter.
    """

    def __init__(
        self,
        engine: SpeechToTextEngine,
        codec: AudioCodecPort,
        gate: InferenceGate,
        thresholds: QualityThresholds,
        max_duration_s: float,
        default_language: str | None,
    ) -> None:
        self._engine = engine
        self._codec = codec
        self._gate = gate
        self._thresholds = thresholds
        self._max_duration_s = max_duration_s
        self._default_language = default_language or None

    async def transcribe(self, audio: bytes, options: TranscriptionOptions) -> TranscriptionResult:
        return await self._gate.run(self._transcribe_blocking, audio, options)

    def _transcribe_blocking(self, audio: bytes, options: TranscriptionOptions) -> TranscriptionResult:
        started = time.perf_counter()
        samples = self._codec.decode(audio, self._engine.sample_rate, self._max_duration_s)
        quality = analyze_audio(samples, self._engine.sample_rate, self._thresholds)

        if options.filter_noise and quality.rejection is not None:
            return self._finish(self._rejected(quality, quality.rejection, started))

        raw = self._engine.transcribe(samples, dataclasses.replace(options, language=self._resolve_language(options.language)))
        segments = clean_segments(raw.segments)
        text = " ".join(s.text for s in segments).strip()

        rejection: RejectionReason | None = None
        if not text:
            rejection = RejectionReason.NO_SPEECH_DETECTED
        elif options.filter_noise and not is_meaningful(text, raw.language):
            rejection = RejectionReason.NOT_MEANINGFUL
        if rejection is not None:
            log.info("wiskro_stt_filtered", extra={"reason": rejection.value, "text": text[:120]})
            return self._finish(self._rejected(quality, rejection, started, raw.language, raw.language_probability))

        return self._finish(TranscriptionResult(
            text=text,
            language=raw.language,
            language_probability=round(raw.language_probability, 3),
            speech_duration_s=round(raw.speech_duration_s, 3),
            segments=segments,
            quality=quality,
            model=self._engine.model_id,
            processing_ms=self._elapsed_ms(started),
        ))

    def _resolve_language(self, requested: str | None) -> str | None:
        """None → server default; AUTO_LANGUAGE → None (engine detects)."""
        if requested is None:
            return self._default_language
        return None if requested == AUTO_LANGUAGE else requested

    def _rejected(
        self,
        quality: AudioQuality,
        reason: RejectionReason,
        started: float,
        language: str | None = None,
        language_probability: float | None = None,
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text="",
            language=language,
            language_probability=round(language_probability, 3) if language_probability is not None else None,
            speech_duration_s=0.0,
            segments=(),
            quality=quality,
            model=self._engine.model_id,
            processing_ms=self._elapsed_ms(started),
            rejection=reason,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)

    @staticmethod
    def _finish(result: TranscriptionResult) -> TranscriptionResult:
        duration = result.quality.duration_s
        log.info("wiskro_stt_completed", extra={
            "audio_s": duration,
            "processing_ms": result.processing_ms,
            "rtf": round(result.processing_ms / 1000 / duration, 3) if duration else None,
            "language": result.language,
            "chars": len(result.text),
            "rejection": result.rejection.value if result.rejection else None,
        })
        return result
