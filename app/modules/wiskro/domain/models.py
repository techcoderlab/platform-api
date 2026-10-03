# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.domain.models
# Layer    : Domain
# Pillar   : P1 Architecture (pure value objects, zero framework deps),
#            P8 Code Quality (immutable, typed contracts)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from app.modules.wiskro.domain.errors import InvalidVoiceError


# ── Shared ────────────────────────────────────────────────────────────────────

class AudioFormat(str, Enum):
    """Output containers the TTS pipeline can render."""
    MP3  = "mp3"
    WAV  = "wav"
    OPUS = "opus"
    FLAC = "flac"

    @property
    def media_type(self) -> str:
        return _MEDIA_TYPES[self]

    @property
    def extension(self) -> str:
        return "ogg" if self is AudioFormat.OPUS else self.value


_MEDIA_TYPES = {
    AudioFormat.MP3:  "audio/mpeg",
    AudioFormat.WAV:  "audio/wav",
    AudioFormat.OPUS: "audio/ogg",
    AudioFormat.FLAC: "audio/flac",
}


# ── Speech-to-Text ────────────────────────────────────────────────────────────

class SttTask(str, Enum):
    TRANSCRIBE = "transcribe"
    TRANSLATE  = "translate"   # any language -> English text


class RejectionReason(str, Enum):
    """Why a clip produced no transcript (returned instead of a silent empty string)."""
    TOO_SHORT              = "too_short"
    SILENCE                = "silence"
    LOW_SNR                = "low_snr"
    LOW_SPEECH_BAND_ENERGY = "low_speech_band_energy"
    NO_SPEECH_DETECTED     = "no_speech_detected"
    NOT_MEANINGFUL         = "not_meaningful"


@dataclass(frozen=True)
class AudioQuality:
    """Acoustic measurements taken before inference (all levels in dBFS)."""
    duration_s:        float
    speech_level_dbfs: float
    noise_floor_dbfs:  float
    snr_db:            float
    speech_band_ratio: float
    rejection:         RejectionReason | None = None


AUTO_LANGUAGE = "auto"


@dataclass(frozen=True)
class TranscriptionOptions:
    language:        str | None = None   # None = server default, AUTO_LANGUAGE = detect
    task:            SttTask    = SttTask.TRANSCRIBE
    initial_prompt:  str | None = None   # context / spelling hints
    hotwords:        str | None = None   # boost rare terms
    word_timestamps: bool       = False
    filter_noise:    bool       = True   # acoustic gate + meaningfulness filter


@dataclass(frozen=True)
class TranscribedWord:
    start:       float
    end:         float
    text:        str
    probability: float


@dataclass(frozen=True)
class TranscribedSegment:
    start:             float
    end:               float
    text:              str
    avg_logprob:       float
    no_speech_prob:    float
    compression_ratio: float
    words:             tuple[TranscribedWord, ...] = ()


@dataclass(frozen=True)
class EngineTranscription:
    """Raw engine output, before transcript filtering."""
    segments:             tuple[TranscribedSegment, ...]
    language:             str
    language_probability: float
    speech_duration_s:    float


@dataclass(frozen=True)
class TranscriptionResult:
    text:                 str
    language:             str | None
    language_probability: float | None
    speech_duration_s:    float
    segments:             tuple[TranscribedSegment, ...]
    quality:              AudioQuality
    model:                str
    processing_ms:        float
    rejection:            RejectionReason | None = None


# ── Text-to-Speech ────────────────────────────────────────────────────────────

_VOICE_NAME = re.compile(r"^[a-z]{2}_[a-z0-9]+$")
MAX_BLEND_VOICES = 4


@dataclass(frozen=True)
class VoiceBlend:
    """One voice, or a weighted mix of voices, parsed from a spec string.

    Accepted specs: ``"af_bella"``, ``"am_liam,am_fenrir"`` (equal weights),
    ``"af_bella:0.7,af_sky:0.3"`` (explicit weights, normalised to sum 1).
    """
    components: tuple[tuple[str, float], ...]

    @classmethod
    def parse(cls, spec: str) -> VoiceBlend:
        parts = [p.strip() for p in spec.strip().lower().split(",") if p.strip()]
        if not parts:
            raise InvalidVoiceError("Voice must not be empty.")
        if len(parts) > MAX_BLEND_VOICES:
            raise InvalidVoiceError(f"At most {MAX_BLEND_VOICES} voices can be blended.")

        weighted: dict[str, float] = {}
        for part in parts:
            name, _, raw_weight = part.partition(":")
            name = name.strip()
            if not _VOICE_NAME.match(name):
                raise InvalidVoiceError(f"Invalid voice name {name!r}.")
            try:
                weight = float(raw_weight) if raw_weight.strip() else 1.0
            except ValueError:
                raise InvalidVoiceError(f"Invalid weight for voice {name!r}.") from None
            if not 0 < weight <= 100:
                raise InvalidVoiceError(f"Weight for voice {name!r} must be in (0, 100].")
            weighted[name] = weighted.get(name, 0.0) + weight

        total = sum(weighted.values())
        return cls(tuple((name, round(w / total, 4)) for name, w in weighted.items()))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.components)

    @property
    def canonical(self) -> str:
        if len(self.components) == 1:
            return self.components[0][0]
        return ",".join(f"{name}:{weight:g}" for name, weight in self.components)


@dataclass(frozen=True)
class SynthesisOptions:
    text:         str
    voice:        str | None  = None   # None = configured default
    speed:        float       = 1.0
    lang:         str | None  = None   # None = inferred from the voice
    audio_format: AudioFormat = AudioFormat.MP3
    prosody:      bool        = True   # sentence-aware dynamic pacing


@dataclass(frozen=True)
class SpeechPart:
    """A sentence to speak, with its pacing multiplier."""
    text:         str
    speed_factor: float = 1.0


@dataclass(frozen=True)
class PausePart:
    seconds: float


ScriptPart = SpeechPart | PausePart


@dataclass(frozen=True)
class SynthesisResult:
    audio:         bytes
    audio_format:  AudioFormat
    sample_rate:   int
    duration_s:    float
    voice:         str
    lang:          str
    processing_ms: float
    cached:        bool = field(default=False, compare=False)
