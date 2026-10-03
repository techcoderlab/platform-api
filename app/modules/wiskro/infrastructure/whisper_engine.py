# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.infrastructure.whisper_engine
# Layer    : Infrastructure
# Pillar   : P4 Performance (CTranslate2 int8 on CPU, ~4x faster and ~3x leaner
#            than PyTorch Whisper), P6 Resilience (built-in ONNX Silero VAD,
#            anti-hallucination decoding)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.core.errors import ValidationError
from app.modules.wiskro.domain.errors import EngineUnavailableError
from app.modules.wiskro.domain.models import (
    EngineTranscription,
    SttTask,
    TranscribedSegment,
    TranscribedWord,
    TranscriptionOptions,
)
from app.modules.wiskro.domain.ports import SpeechToTextEngine
from app.modules.wiskro.infrastructure.lazy_model import LazyModel
from app.modules.wiskro.infrastructure.model_store import ModelStore

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

# VAD tuned for conversational turns: tolerate short pauses, keep word edges
_VAD_PARAMETERS = {"min_silence_duration_ms": 500, "speech_pad_ms": 200}
# Skip silent gaps > N s that Whisper would otherwise fill with invented text
_HALLUCINATION_SILENCE_S = 2.0


class FasterWhisperEngine(LazyModel["WhisperModel"], SpeechToTextEngine):
    """faster-whisper (CTranslate2) adapter for the STT port."""

    sample_rate = 16000

    def __init__(self, store: ModelStore, model: str, compute_type: str, cpu_threads: int, beam_size: int) -> None:
        super().__init__(name=f"whisper:{model}")
        self.model_id = f"faster-whisper/{model}"
        self._store = store
        self._model_name = model
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._beam_size = beam_size
        self._model_path: str | None = None
        self._english_only = model.endswith(".en")

    def _ensure_assets(self) -> None:
        if Path(self._model_name).is_dir():   # pre-converted local CTranslate2 model
            self._model_path = self._model_name
            return
        from faster_whisper import download_model
        try:
            self._model_path = download_model(
                self._model_name,
                cache_dir=str(self._store.directory("whisper")),
                local_files_only=not self._store.allow_download,
            )
        except Exception as exc:
            hint = "" if self._store.allow_download else " (WISKRO_ALLOW_DOWNLOAD is disabled)"
            raise EngineUnavailableError(f"Whisper model {self._model_name!r} is unavailable{hint}: {exc}") from exc

    def _load(self) -> "WhisperModel":
        from faster_whisper import WhisperModel
        return WhisperModel(
            self._model_path,
            device="cpu",
            compute_type=self._compute_type,
            cpu_threads=self._cpu_threads,
            num_workers=1,
        )

    def transcribe(self, audio: np.ndarray, options: TranscriptionOptions) -> EngineTranscription:
        if self._english_only and (options.language not in (None, "en") or options.task is SttTask.TRANSLATE):
            raise ValidationError(
                f"Model {self._model_name!r} is English-only; use a multilingual model "
                "(e.g. WISKRO_STT_MODEL=base) for other languages or translation."
            )

        with self.use() as model:
            segments, info = model.transcribe(
                audio,
                language="en" if self._english_only else options.language,
                task=options.task.value,
                beam_size=self._beam_size,
                vad_filter=True,
                vad_parameters=_VAD_PARAMETERS,
                condition_on_previous_text=False,   # stops one bad window from looping into the next
                initial_prompt=options.initial_prompt,
                hotwords=options.hotwords,
                word_timestamps=options.word_timestamps,
                hallucination_silence_threshold=_HALLUCINATION_SILENCE_S if options.word_timestamps else None,
            )
            # Segments are a lazy generator: consume while the model is pinned
            parsed = tuple(self._to_segment(s) for s in segments)

        return EngineTranscription(
            segments=parsed,
            language=info.language,
            language_probability=float(info.language_probability),
            speech_duration_s=float(info.duration_after_vad),
        )

    @staticmethod
    def _to_segment(segment) -> TranscribedSegment:
        words = tuple(
            TranscribedWord(start=round(w.start, 3), end=round(w.end, 3), text=w.word.strip(), probability=round(w.probability, 3))
            for w in (segment.words or ())
        )
        return TranscribedSegment(
            start=round(segment.start, 3),
            end=round(segment.end, 3),
            text=segment.text.strip(),
            avg_logprob=float(segment.avg_logprob),
            no_speech_prob=float(segment.no_speech_prob),
            compression_ratio=float(segment.compression_ratio) if math.isfinite(segment.compression_ratio) else 0.0,
            words=words,
        )
