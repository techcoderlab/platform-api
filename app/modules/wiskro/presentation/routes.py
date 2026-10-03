# ─────────────────────────────────────────────────────
# Module   : app.modules.wiskro.presentation.routes
# Layer    : Presentation
# Pillar   : P1 Architecture (HTTP in/out only, delegates to Application layer),
#            P2 Security (bounded uploads, validated inputs),
#            P6 Resilience (typed errors → 4xx/5xx via global handlers)
# ─────────────────────────────────────────────────────
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError as PydanticValidationError

from app.core.config import settings
from app.core.errors import PayloadTooLargeError, ValidationError
from app.core.responses import ErrorResponse, SuccessResponse, success_response
from app.modules.wiskro.application.stt_service import SpeechToTextService
from app.modules.wiskro.application.tts_service import TextToSpeechService
from app.modules.wiskro.bootstrap import WiskroRuntime
from app.modules.wiskro.domain.models import AudioFormat, SttTask, TranscriptionResult
from app.modules.wiskro.presentation.schemas import (
    AudioQualityResponse,
    SegmentResponse,
    SynthesizeRequest,
    TranscriptionForm,
    TranscriptionResponse,
    WordResponse,
)

router = APIRouter(tags=["Wiskro"])

_MAX_UPLOAD_BYTES = settings.WISKRO_STT_MAX_UPLOAD_MB * 1024 * 1024
_ERRORS = {
    400: {"model": ErrorResponse, "description": "Invalid input"},
    413: {"model": ErrorResponse, "description": "Payload too large"},
    429: {"model": ErrorResponse, "description": "Engine at capacity — retry shortly"},
    503: {"model": ErrorResponse, "description": "Model unavailable or queue timeout"},
}


# ── Dependency accessors ─────────────────────────────────────────────────────
# WiskroRuntime is attached to app.state during lifespan (no module-level singleton).

def _get_runtime(request: Request) -> WiskroRuntime:
    runtime: WiskroRuntime | None = getattr(request.app.state, "wiskro", None)
    if runtime is None:
        raise RuntimeError("WiskroRuntime not initialized — check app lifespan.")
    return runtime


def get_stt_service(request: Request) -> SpeechToTextService:
    return _get_runtime(request).stt


def get_tts_service(request: Request) -> TextToSpeechService:
    return _get_runtime(request).tts


def transcription_form(
    language: str | None = Form(None),
    task: SttTask = Form(SttTask.TRANSCRIBE),
    initial_prompt: str | None = Form(None),
    hotwords: str | None = Form(None),
    word_timestamps: bool = Form(False),
    filter_noise: bool = Form(True),
) -> TranscriptionForm:
    try:
        return TranscriptionForm(
            language=language,
            task=task,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
            word_timestamps=word_timestamps,
            filter_noise=filter_noise,
        )
    except PydanticValidationError as exc:
        raise ValidationError("; ".join(err["msg"] for err in exc.errors())) from None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an upload into RAM, refusing anything over `max_bytes` without trusting headers."""
    too_large = PayloadTooLargeError(f"Audio exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    if upload.size is not None and upload.size > max_bytes:
        raise too_large
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise too_large
    return data


def _to_transcription_response(result: TranscriptionResult, filename: str | None, words: bool) -> TranscriptionResponse:
    segments = [
        SegmentResponse(
            start=s.start,
            end=s.end,
            text=s.text,
            confidence=round(math.exp(s.avg_logprob), 3),
            words=[WordResponse(start=w.start, end=w.end, word=w.text, probability=w.probability) for w in s.words]
            if words else None,
        )
        for s in result.segments
    ]
    q = result.quality
    return TranscriptionResponse(
        text=result.text,
        rejected=result.rejection is not None,
        rejection_reason=result.rejection.value if result.rejection else None,
        language=result.language,
        language_probability=result.language_probability,
        duration_s=q.duration_s,
        speech_duration_s=result.speech_duration_s,
        segments=segments,
        audio_quality=AudioQualityResponse(
            duration_s=q.duration_s,
            speech_level_dbfs=q.speech_level_dbfs,
            noise_floor_dbfs=q.noise_floor_dbfs,
            snr_db=q.snr_db,
            speech_band_ratio=q.speech_band_ratio,
        ),
        model=result.model,
        processing_ms=result.processing_ms,
        filename=filename[:255] if filename else None,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/tts",
    response_class=Response,
    responses={
        200: {
            "content": {audio_format.media_type: {} for audio_format in AudioFormat},
            "description": "Encoded speech. Metadata in X-Audio-* headers.",
        },
        **_ERRORS,
    },
    summary="Text to speech",
    description="Synthesises natural speech with Kokoro-82M. Returns the audio file directly.",
)
async def synthesize_speech(
    body: SynthesizeRequest,
    service: TextToSpeechService = Depends(get_tts_service),
) -> Response:
    result = await service.synthesize(body.to_options())
    return Response(
        content=result.audio,
        media_type=result.audio_format.media_type,
        headers={
            "Content-Disposition": f'inline; filename="speech.{result.audio_format.extension}"',
            "Cache-Control": "no-store",
            "X-Audio-Duration": f"{result.duration_s:.3f}",
            "X-Audio-Sample-Rate": str(result.sample_rate),
            "X-Voice": result.voice,
            "X-Lang": result.lang,
            "X-Cache": "HIT" if result.cached else "MISS",
            "X-Synthesis-Ms": f"{result.processing_ms:.1f}",
        },
    )


@router.post(
    "/stt",
    response_model=SuccessResponse[TranscriptionResponse],
    responses=_ERRORS,
    summary="Speech to text",
    description=(
        "Transcribes an uploaded recording (WAV, MP3, WEBM, OGG, M4A, FLAC, ...) with Whisper. "
        "Noise, silence and hallucinations are rejected with an explicit `rejection_reason`."
    ),
)
async def transcribe_speech(
    request: Request,
    audio: UploadFile = File(..., description="Audio recording"),
    form: TranscriptionForm = Depends(transcription_form),
    service: SpeechToTextService = Depends(get_stt_service),
):
    data = await _read_upload(audio, _MAX_UPLOAD_BYTES)
    result = await service.transcribe(data, form.to_options())
    payload = _to_transcription_response(result, audio.filename, words=form.word_timestamps)
    return success_response(
        data=payload.model_dump(exclude_none=True),
        request_id=getattr(request.state, "request_id", None),
    )
