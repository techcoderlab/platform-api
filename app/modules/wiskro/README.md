# Wiskro — Speech-to-Text & Text-to-Speech

CPU-only voice module for the platform API: **Whisper** (STT) and **Kokoro-82M** (TTS), tuned to run on the Haier Core M server (2 cores, 4 GB RAM, no GPU).

| Route | Purpose |
| :--- | :--- |
| `POST /api/v1/wiskro/stt` | Upload a recording → filtered transcript (JSON) |
| `POST /api/v1/wiskro/tts` | Text → speech audio file (MP3 / WAV / Opus / FLAC) |

Both routes sit behind the global `X-API-Key` middleware. Enable with `ENABLE_WISKRO=true`.

---

## What it does

**STT pipeline:** decode in RAM (any FFmpeg format, incl. browser WEBM/Opus and iPhone M4A) → acoustic noise gate (duration, loudness, SNR, speech-band energy) → Silero VAD + Whisper → hallucination and repetition cleanup → meaningfulness filter. Rejected clips return `200` with `rejected: true` and an explicit `rejection_reason`, so voice agents can re-prompt instead of guessing why the text is empty.

**TTS pipeline:** script parsing (markdown/emoji stripping, `[PAUSE]` markers, sentence splitting, prosody) → per-sentence Kokoro synthesis → de-click fades → loudness mastering → in-memory encoding → render cache.

Audio is never written to disk (zero persistence of voice data).

## Quick start

```bash
# Text to speech
curl -X POST "http://localhost:8012/api/v1/wiskro/tts" \
     -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
     -d '{"text": "Hello! [PAUSE] Your order has shipped.", "voice": "am_adam"}' \
     --output speech.mp3

# Speech to text
curl -X POST "http://localhost:8012/api/v1/wiskro/stt" \
     -H "X-API-Key: $API_KEY" \
     -F "audio=@recording.webm"
```

The first request after startup (or after an idle unload) loads the model, which takes a few seconds; later requests are warm. Model files are downloaded once into `WISKRO_MODEL_DIR` at startup.

## What changed vs. the original service

| Area | Original | Wiskro |
| :--- | :--- | :--- |
| Runtime | PyTorch + openai-whisper + torch Kokoro (> 2 GB RAM) | CTranslate2 + ONNX Runtime, int8 (~580 MB with both models loaded, ~70 MB idle) |
| VAD | `torch.hub` download from GitHub at runtime | Silero VAD bundled (ONNX) in faster-whisper |
| Decoding | ffmpeg subprocess pipe (fails on M4A/MP4 with trailing index) | PyAV in-process, seekable buffer, tolerates corrupt packets |
| SNR check | 10th percentile of raw samples (≈ 0 for any audio, so it never rejected anything) | Frame-RMS speech level vs noise floor |
| Empty result | `""`, no reason | `rejected` + `rejection_reason` + audio quality metrics |
| Hallucinations | Word repetition regex only | Phrase/word loop collapse, known subtitle phrases, `condition_on_previous_text=False` |
| TTS `speed` | Ignored | Applied, combined with prosody under an intelligibility ceiling |
| Mastering | Normalise to 0.98 then +1 dB (clipped) | Loudness target with -1 dBFS peak ceiling |
| Voices | Single voice | Validated; weighted blends (`am_liam,am_fenrir`); 9 languages |
| Formats | MP3 only | MP3, WAV, Opus, FLAC |
| Concurrency | Unbounded queue in front of a global lock | Bounded slots, 429 on overload, 503 on queue timeout |
| Memory | Models resident forever | Lazy load, unload after idle, memory released to the OS |
| Model files | Unverified downloads | SHA-256 pinned, atomic writes, retried |

## Documentation

- [API Reference](docs/api_reference.md): request fields, responses, headers and error codes.
- [Configuration](docs/configuration.md): environment variables and how to size them for the Haier server.
- [Architecture](docs/architecture.md): layers, request lifecycle, concurrency and memory model.
