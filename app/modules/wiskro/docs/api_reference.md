# API Reference

Base path: `/api/v1/wiskro`. Every request needs the `X-API-Key` header (or `?api_key=`).

---

## `POST /tts` — Text to speech

**Request** (`application/json`):

| Field | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `text` | string | — | 1 to `WISKRO_TTS_MAX_CHARS` characters. Supports script markup (below). |
| `voice` | string | `WISKRO_TTS_DEFAULT_VOICE` | A voice id (`af_heart`), an equal blend (`am_liam,am_fenrir`) or a weighted blend (`af_bella:0.7,af_sky:0.3`). Up to 4 voices. |
| `speed` | float | `1.0` | 0.5–2.0. |
| `lang` | string | from voice | `en-us`, `en-gb`, `es`, `fr-fr`, `hi`, `it`, `ja`, `pt-br`, `cmn`. Inferred from the voice prefix (`a`→en-us, `b`→en-gb, …). |
| `format` | string | `mp3` | `mp3`, `wav`, `opus` (Ogg), `flac`. |
| `prosody` | bool | `true` | Dynamic pacing per sentence (see below). |

**Response:** `200` with the audio file as the body.

| Header | Example | Meaning |
| :--- | :--- | :--- |
| `Content-Type` | `audio/mpeg` | `audio/mpeg`, `audio/wav`, `audio/ogg`, `audio/flac` |
| `X-Audio-Duration` | `5.953` | Seconds of audio |
| `X-Audio-Sample-Rate` | `24000` | Hz (Opus is resampled to 48 kHz inside the container) |
| `X-Voice` / `X-Lang` | `am_adam` / `en-us` | Resolved voice spec and language |
| `X-Cache` | `HIT` | `HIT` when served from the render cache |
| `X-Synthesis-Ms` | `2113.4` | Inference + encoding time (0 on cache hits) |

### Script markup

The engine reads LLM output safely: markdown (`**bold**`, headings, bullets, links), emoji and control characters are stripped, URLs are spoken as their domain, and headings and bullets become their own sentences.

| Markup | Effect |
| :--- | :--- |
| `[PAUSE]` | 0.5 s silence |
| `[PAUSE 1.5]`, `[PAUSE:2s]`, `[PAUSE 800ms]` | Custom silence (max 5 s; consecutive pauses merge) |
| `...` | Mid-sentence micro-pause (never splits the sentence) |
| `!` at sentence end | Energetic delivery, slightly faster |
| Blank line | Paragraph pause (0.35 s) |

**Prosody** (when `prosody=true`) multiplies `speed` per sentence: `!` ×1.15, short (< 50 chars) ×1.05, long (> 150 chars) ×0.9, otherwise ×0.95, and the final sentence ×0.92 for a natural ending. Boosts stop at 1.2× (or at your `speed` if it is higher), because Kokoro starts clipping word onsets above that.

#### Recommended LLM system-prompt rules

> 1. Use `[PAUSE]` between distinct ideas or topic shifts.
> 2. Use `...` for natural mid-sentence hesitation ("Actually... let me check").
> 3. Hyphenate acronyms so they are spelled out: "A-P-I", "N-8-N", "JAY-SON".
> 4. End punchy, high-energy sentences with `!`.
> 5. Keep sentences under 150 characters.
> 6. Plain text only: no markdown, asterisks, hashtags or emoji.

---

## `POST /stt` — Speech to text

**Request** (`multipart/form-data`):

| Field | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `audio` | file | — | WAV, MP3, WEBM, OGG, M4A/MP4, FLAC, AAC, … up to `WISKRO_STT_MAX_UPLOAD_MB` and `WISKRO_STT_MAX_DURATION_SECONDS`. |
| `language` | string | `WISKRO_STT_DEFAULT_LANGUAGE` | ISO-639 code, or `auto` to detect. `.en` models accept English only. |
| `task` | string | `transcribe` | `translate` = any language → English (multilingual models only). |
| `initial_prompt` | string | — | Context or spelling hints, ≤ 1000 chars ("Customer is ordering from Wiskro Pizza."). |
| `hotwords` | string | — | Rare terms to boost, ≤ 500 chars. |
| `word_timestamps` | bool | `false` | Adds per-word timings; also enables silence-hallucination skipping. |
| `filter_noise` | bool | `true` | Acoustic gate + meaningfulness filter. Disable for dictation of short or unusual utterances. |

**Response** `200`:

```json
{
  "status": "success",
  "data": {
    "text": "Great news. Your package arrived early.",
    "rejected": false,
    "language": "en",
    "language_probability": 1.0,
    "duration_s": 5.953,
    "speech_duration_s": 5.74,
    "segments": [
      {"start": 0.0, "end": 2.1, "text": "Great news.", "confidence": 0.81}
    ],
    "audio_quality": {
      "duration_s": 5.953, "speech_level_dbfs": -16.1, "noise_floor_dbfs": -62.3,
      "snr_db": 46.2, "speech_band_ratio": 0.69
    },
    "model": "faster-whisper/base.en",
    "processing_ms": 1030.7,
    "filename": "recording.webm"
  },
  "request_id": "c75d0961-…"
}
```

When a clip is rejected, `text` is `""`, `segments` is empty, and `rejection_reason` is one of:

| Reason | Meaning |
| :--- | :--- |
| `too_short` | Shorter than `WISKRO_STT_MIN_DURATION_SECONDS` |
| `silence` | Speech frames quieter than `WISKRO_STT_MIN_SPEECH_RMS` |
| `low_snr` | Speech barely above the noise floor (`WISKRO_STT_MIN_SNR_DB`) |
| `low_speech_band_energy` | Energy mostly outside 300–3400 Hz (hum, rumble, music) |
| `no_speech_detected` | VAD/Whisper found no speech, or only known hallucinations |
| `not_meaningful` | Stutter, filler or gibberish (English dictionary check) |

---

## Errors

Errors use the platform envelope `{"status": "error", "error": "...", "request_id": "..."}`. Schema violations use FastAPI's standard `422` shape.

| Status | When |
| :--- | :--- |
| `400` | Undecodable/corrupt audio, playlists or manifests, unknown voice or language, nothing speakable, a language the model cannot do |
| `401` | Missing or invalid API key |
| `413` | Upload larger than the size limit, or audio longer than the duration limit |
| `422` | Request schema violation (missing field, out-of-range speed, unknown JSON field) |
| `429` | Engine busy and its wait queue full (`WISKRO_MAX_PENDING`). Retry with backoff. |
| `503` | Model files unavailable (download failed or disabled), or queue wait exceeded `WISKRO_QUEUE_TIMEOUT_SECONDS` |
