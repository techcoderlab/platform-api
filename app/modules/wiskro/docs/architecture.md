# Architecture & Design

Wiskro follows the same Clean Architecture layout as `web_scraper`. Dependencies point inward only.

```
presentation/   FastAPI routes + Pydantic DTOs          (HTTP in/out, validation)
application/    Use cases and pure audio/text logic     (no framework, no ML imports)
domain/         Value objects, ports (ABCs), errors     (zero external deps besides numpy)
infrastructure/ Adapters: faster-whisper, kokoro-onnx,  (all heavy libraries live here,
                PyAV codec, model store, lazy loader     imported lazily)
bootstrap.py    Composition root: wires adapters into services, owns background tasks
```

| Package | Modules |
| :--- | :--- |
| `domain` | `models` (options/results/voice blends), `ports` (`SpeechToTextEngine`, `TextToSpeechEngine`, `AudioCodecPort`, `ManagedModel`), `errors` |
| `application` | `stt_service`, `tts_service`, `tts_script` (markup → sentences/pauses), `transcript_filter`, `audio_quality` (noise gate), `audio_mastering`, `inference_gate`, `render_cache` |
| `infrastructure` | `whisper_engine`, `kokoro_engine`, `audio_codec`, `model_store`, `lazy_model` |

Swapping an engine (e.g. whisper.cpp or Piper) means writing one adapter that implements the port; services and routes stay unchanged.

## Request lifecycle

**STT:** route reads the upload (bounded) → `SpeechToTextService.transcribe` → `InferenceGate` admits or rejects → worker thread: `PyAVAudioCodec.decode` (16 kHz mono float32) → `analyze_audio` noise gate → `FasterWhisperEngine.transcribe` (VAD + decoding) → `clean_segments` → `is_meaningful` → result DTO.

**TTS:** route validates JSON → `TextToSpeechService.synthesize` → render-cache lookup (served without queueing on a hit) → `InferenceGate` → worker thread: `build_script` → per sentence `KokoroOnnxEngine.synthesize` → fades and gaps → `master` → `PyAVAudioCodec.encode` → cache → binary response.

## Concurrency model

- The event loop never runs inference. Each engine has an `InferenceGate`: an `asyncio.Semaphore` (`*_MAX_CONCURRENCY`) plus a **dedicated** `ThreadPoolExecutor`, so ML work never competes with the default executor used elsewhere in the app.
- Admission control: at most `WISKRO_MAX_PENDING` callers wait per engine. Beyond that, the request fails immediately with `429` instead of accumulating in memory. Waiting longer than `WISKRO_QUEUE_TIMEOUT_SECONDS` returns `503`.
- STT and TTS gates are independent, so a long synthesis never blocks transcription.
- espeak phonemisation is serialised inside kokoro-onnx (it keeps global C state); ONNX inference itself is thread-safe.

## Memory model

- `LazyModel` (template method) loads a model on first use under a lock and tracks an in-use counter, so it can never be unloaded mid-inference.
- A reaper task unloads models idle for `WISKRO_IDLE_UNLOAD_SECONDS`, then runs `gc.collect()` and `malloc_trim(0)` so freed pages go back to the OS (glibc).
- ONNX Runtime runs with the CPU memory arena disabled, so activation buffers are released after every call instead of being held at their peak.
- STT audio is decoded straight to float32 in RAM; the noise gate's FFT work is capped at 2000 frames regardless of clip length.
- The render cache is bounded by bytes (`WISKRO_TTS_CACHE_MB`), and no single entry may exceed a quarter of it.

## Startup and resilience

- `WiskroRuntime.start()` returns immediately. A background task downloads model files and, with `WISKRO_PRELOAD`, loads them.
- `ModelStore` downloads to `*.part`, verifies size and SHA-256, then atomically renames into place, retrying transient network errors with exponential backoff. Existing files are re-verified once per process, and corrupt ones are re-fetched.
- Failures degrade the module instead of crashing the app: affected requests return `503` with the cause, and the next request retries.
- Untrusted media: playlist and manifest uploads are rejected by signature, FFmpeg nested I/O is limited to the in-memory stream (`protocol_whitelist=pipe`), and indirection demuxers (HLS, concat, DASH, …) are refused. This blocks SSRF and local-file reads through crafted uploads.

## Why these engines

The original service used PyTorch (openai-whisper + torch Kokoro + torch.hub Silero). On a 4 GB, 2-core, GPU-less host that stack needs over 2 GB of RAM and a multi-GB image. The same models run here on CTranslate2 (faster-whisper) and ONNX Runtime (kokoro-onnx) with int8 weights: no PyTorch, a fraction of the memory, faster on CPU, and with the VAD bundled instead of fetched from GitHub at runtime.
