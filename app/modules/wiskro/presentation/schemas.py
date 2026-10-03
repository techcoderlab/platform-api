# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.presentation.schemas
# Layer    : Presentation
# Pillar   : P2 Security (input validation at the boundary),
#            P8 Code Quality (strict Pydantic v2 DTOs)
# ─────────────────────────────────────────────────────
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.core.config import settings
from app.modules.wiskro.domain.errors import InvalidVoiceError
from app.modules.wiskro.domain.models import (
    AUTO_LANGUAGE,
    AudioFormat,
    SttTask,
    SynthesisOptions,
    TranscriptionOptions,
    VoiceBlend,
)

# DATA: PII — request text and transcripts may contain personal data; never log them in full.


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


# ── Request DTOs ──────────────────────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    """Inbound text-to-speech request."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        min_length=1,
        max_length=settings.WISKRO_TTS_MAX_CHARS,
        description="Text to speak. Supports [PAUSE] / [PAUSE 1.5] markers; markdown and emoji are stripped.",
        examples=["Hello! [PAUSE] Your order has shipped... it arrives tomorrow."],
    )
    voice: str | None = Field(
        default=None,
        max_length=200,
        description="Voice id, or a blend: 'am_liam,am_fenrir' (equal) / 'af_bella:0.7,af_sky:0.3'.",
        examples=["am_adam", "af_bella:0.6,af_sarah:0.4"],
    )
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Base speaking rate.")
    lang: str | None = Field(
        default=None,
        max_length=8,
        description="Phonemiser language (en-us, en-gb, es, fr-fr, hi, it, ja, pt-br, cmn). Inferred from the voice if omitted.",
    )
    format: AudioFormat = Field(default=AudioFormat.MP3, description="Output container.")
    prosody: bool = Field(default=True, description="Sentence-aware dynamic pacing (faster on '!', slower final sentence).")

    @field_validator("text")
    @classmethod
    def _require_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must contain non-whitespace characters.")
        return value

    @field_validator("voice")
    @classmethod
    def _validate_voice(cls, value: str | None) -> str | None:
        value = _blank_to_none(value)
        if value is not None:
            try:
                VoiceBlend.parse(value)
            except InvalidVoiceError as exc:
                raise ValueError(exc.message) from None
        return value

    @field_validator("lang")
    @classmethod
    def _normalize_lang(cls, value: str | None) -> str | None:
        value = _blank_to_none(value)
        return value.lower() if value else None

    def to_options(self) -> SynthesisOptions:
        return SynthesisOptions(
            text=self.text,
            voice=self.voice,
            speed=self.speed,
            lang=self.lang,
            audio_format=self.format,
            prosody=self.prosody,
        )


class TranscriptionForm(BaseModel):
    """Form fields accompanying an STT upload."""

    language: str | None = Field(
        default=None, pattern=rf"^(?:[a-z]{{2,3}}|{AUTO_LANGUAGE})$",
        description=f"ISO-639 code (e.g. 'en'). Omit to use the server default; send '{AUTO_LANGUAGE}' to detect.",
    )
    task: SttTask = SttTask.TRANSCRIBE
    initial_prompt: str | None = Field(default=None, max_length=1000, description="Context or spelling hints.")
    hotwords: str | None = Field(default=None, max_length=500, description="Rare terms to boost (names, jargon).")
    word_timestamps: bool = False
    filter_noise: bool = Field(default=True, description="Reject noise/silence and meaningless transcripts.")

    @field_validator("language", "initial_prompt", "hotwords", mode="before")
    @classmethod
    def _normalize_text_fields(cls, value: str | None, info: ValidationInfo) -> str | None:
        value = _blank_to_none(value)
        return value.lower() if value and info.field_name == "language" else value

    def to_options(self) -> TranscriptionOptions:
        return TranscriptionOptions(
            language=self.language,
            task=self.task,
            initial_prompt=self.initial_prompt,
            hotwords=self.hotwords,
            word_timestamps=self.word_timestamps,
            filter_noise=self.filter_noise,
        )


# ── Response DTOs ─────────────────────────────────────────────────────────────

class WordResponse(BaseModel):
    start: float
    end: float
    word: str
    probability: float


class SegmentResponse(BaseModel):
    start: float
    end: float
    text: str
    confidence: float = Field(..., description="exp(avg_logprob), 0-1.")
    words: list[WordResponse] | None = None


class AudioQualityResponse(BaseModel):
    duration_s: float
    speech_level_dbfs: float
    noise_floor_dbfs: float
    snr_db: float
    speech_band_ratio: float


class TranscriptionResponse(BaseModel):
    text: str = Field(..., description="Transcript; empty when the clip was rejected.")
    rejected: bool
    rejection_reason: str | None = Field(
        default=None,
        description="too_short | silence | low_snr | low_speech_band_energy | no_speech_detected | not_meaningful",
    )
    language: str | None = None
    language_probability: float | None = None
    duration_s: float
    speech_duration_s: float
    segments: list[SegmentResponse] = Field(default_factory=list)
    audio_quality: AudioQualityResponse
    model: str
    processing_ms: float
    filename: str | None = None
