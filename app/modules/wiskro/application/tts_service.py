# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.application.tts_service
# Layer    : Application
# Pillar   : P4 Performance (render cache, single in-memory encode),
#            P6 Resilience (sentence-level isolation of phonemiser failures)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import dataclasses
import hashlib
import time

import numpy as np

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.modules.wiskro.application.audio_mastering import apply_fades, master, silence
from app.modules.wiskro.application.inference_gate import InferenceGate
from app.modules.wiskro.application.render_cache import RenderCache
from app.modules.wiskro.application.tts_script import build_script
from app.modules.wiskro.domain.models import (
    PausePart,
    SynthesisOptions,
    SynthesisResult,
    VoiceBlend,
)
from app.modules.wiskro.domain.ports import AudioCodecPort, TextToSpeechEngine

log = get_logger(__name__)

SENTENCE_GAP_S = 0.1         # breath between sentences (model output is trimmed)
MIN_SPEED, MAX_SPEED = 0.5, 2.0
PROSODY_SPEED_CEILING = 1.2  # prosody boosts stop here; onsets get clipped beyond it


def _effective_speed(base: float, factor: float) -> float:
    """Apply a prosody factor without letting boosts push past intelligibility.

    A caller who explicitly asks for a fast base speed still gets it; prosody just
    stops adding to it above the ceiling.
    """
    speed = base * factor
    if factor > 1.0:
        speed = min(speed, max(base, PROSODY_SPEED_CEILING))
    return min(max(speed, MIN_SPEED), MAX_SPEED)


class TextToSpeechService:
    """Use case: text in → encoded, mastered speech out.

    Pipeline: script (normalise, pauses, sentences, prosody) → per-sentence
    synthesis → join with de-click fades → master → encode → cache.
    """

    def __init__(
        self,
        engine: TextToSpeechEngine,
        codec: AudioCodecPort,
        gate: InferenceGate,
        cache: RenderCache[SynthesisResult],
        default_voice: str,
        model_id: str,
    ) -> None:
        self._engine = engine
        self._codec = codec
        self._gate = gate
        self._cache = cache
        self._default_voice = default_voice
        self._model_id = model_id

    async def synthesize(self, options: SynthesisOptions) -> SynthesisResult:
        voice = VoiceBlend.parse(options.voice or self._default_voice)
        key = self._cache_key(options, voice)

        cached = self._cache.get(key)
        if cached is not None:
            log.info("wiskro_tts_cache_hit", extra={"chars": len(options.text), "voice": voice.canonical})
            return dataclasses.replace(cached, cached=True, processing_ms=0.0)

        result = await self._gate.run(self._synthesize_blocking, options, voice)
        self._cache.put(key, result, len(result.audio))
        return result

    def _synthesize_blocking(self, options: SynthesisOptions, voice: VoiceBlend) -> SynthesisResult:
        started = time.perf_counter()
        sample_rate = self._engine.sample_rate
        lang = self._engine.resolve_lang(voice, options.lang)

        pieces: list[np.ndarray] = []
        spoken = 0
        previous_was_speech = False
        for part in build_script(options.text, prosody=options.prosody):
            if isinstance(part, PausePart):
                pieces.append(silence(part.seconds, sample_rate))
                previous_was_speech = False
                continue
            try:
                audio = self._engine.synthesize(part.text, voice, lang, _effective_speed(options.speed, part.speed_factor))
            except ValueError as exc:
                # Phonemiser produced nothing usable (e.g. a lone symbol): skip the sentence, keep the rest
                log.warning("wiskro_tts_sentence_skipped", extra={"text": part.text[:80], "error": str(exc)})
                continue
            if previous_was_speech:
                pieces.append(silence(SENTENCE_GAP_S, sample_rate))
            pieces.append(apply_fades(audio, sample_rate))
            previous_was_speech = True
            spoken += 1

        if not spoken:
            raise ValidationError("Text contains nothing speakable.")

        samples = master(np.concatenate(pieces))
        encoded = self._codec.encode(samples, sample_rate, options.audio_format)

        result = SynthesisResult(
            audio=encoded,
            audio_format=options.audio_format,
            sample_rate=sample_rate,
            duration_s=round(len(samples) / sample_rate, 3),
            voice=voice.canonical,
            lang=lang,
            processing_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        log.info("wiskro_tts_completed", extra={
            "chars": len(options.text),
            "sentences": spoken,
            "audio_s": result.duration_s,
            "processing_ms": result.processing_ms,
            "rtf": round(result.processing_ms / 1000 / result.duration_s, 3) if result.duration_s else None,
            "voice": result.voice,
            "format": result.audio_format.value,
            "bytes": len(encoded),
        })
        return result

    def _cache_key(self, options: SynthesisOptions, voice: VoiceBlend) -> str:
        fingerprint = "\x1f".join((
            self._model_id, options.text, voice.canonical, f"{options.speed:.3f}",
            options.lang or "", options.audio_format.value, str(options.prosody),
        ))
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
