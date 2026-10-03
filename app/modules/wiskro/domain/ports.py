# ── Domain Ports: ABCs that Infrastructure must implement ────────────────────
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from app.modules.wiskro.domain.models import (
    AudioFormat,
    EngineTranscription,
    TranscriptionOptions,
    VoiceBlend,
)


class ManagedModel(ABC):
    """Lifecycle contract for models that are fetched, loaded and unloaded on demand."""

    name: str

    @property
    @abstractmethod
    def is_loaded(self) -> bool: ...

    @abstractmethod
    def ensure_assets(self) -> None:
        """Make model files available locally (may download). Blocking."""

    @abstractmethod
    def load(self) -> None:
        """Load the model into memory. Blocking, idempotent."""

    @abstractmethod
    def unload_if_idle(self, idle_seconds: float) -> bool:
        """Free the model if unused for `idle_seconds`. Returns True if unloaded."""

    @abstractmethod
    def unload(self) -> None: ...


class SpeechToTextEngine(ManagedModel):
    """Contract for any STT backend (faster-whisper, whisper.cpp, cloud, ...)."""

    sample_rate: int
    model_id: str

    @abstractmethod
    def transcribe(self, audio: np.ndarray, options: TranscriptionOptions) -> EngineTranscription:
        """Transcribe mono float32 PCM at `sample_rate`. Blocking."""


class TextToSpeechEngine(ManagedModel):
    """Contract for any TTS backend (Kokoro ONNX, Piper, cloud, ...)."""

    sample_rate: int

    @abstractmethod
    def resolve_lang(self, voice: VoiceBlend, lang: str | None) -> str:
        """Validate the voice (and language) and return the language to speak in."""

    @abstractmethod
    def synthesize(self, text: str, voice: VoiceBlend, lang: str, speed: float) -> np.ndarray:
        """Render one sentence to mono float32 PCM at `sample_rate`. Blocking."""


class AudioCodecPort(ABC):
    """Contract for audio container decoding/encoding."""

    @abstractmethod
    def decode(self, data: bytes, sample_rate: int, max_seconds: float) -> np.ndarray:
        """Decode any supported container to mono float32 PCM at `sample_rate`."""

    @abstractmethod
    def encode(self, samples: np.ndarray, sample_rate: int, audio_format: AudioFormat) -> bytes:
        """Encode mono float32 PCM into the requested container."""
