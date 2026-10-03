# Configuration Guide

All settings are environment variables parsed into the global `Settings` object (`app/core/config.py`). Defaults are tuned for the Haier Core M server (Intel Core M-5Y10c, 2 cores / 4 threads, 4 GB RAM, no GPU).

## Module & lifecycle

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ENABLE_WISKRO` | `false` | Registers the routes and starts the runtime. When off, no ML library is imported. |
| `WISKRO_MODEL_DIR` | `/app/data/models` | Model cache. **Mount a persistent volume** so files survive container rebuilds. |
| `WISKRO_ALLOW_DOWNLOAD` | `true` | Fetch missing files on demand. Set `false` for air-gapped hosts once the cache is populated. |
| `WISKRO_PRELOAD` | `false` | Load both models at startup. Faster first request, but holds ~500 MB even when idle. |
| `WISKRO_IDLE_UNLOAD_SECONDS` | `900` | Free a model after this many idle seconds (`0` = never). The next request reloads it (~1–3 s). |
| `WISKRO_MAX_PENDING` | `8` | Requests allowed to wait per engine; beyond this → `429`. |
| `WISKRO_QUEUE_TIMEOUT_SECONDS` | `120` | Max wait for an inference slot → `503`. |

## Speech-to-text

| Variable | Default | Description |
| :--- | :--- | :--- |
| `WISKRO_STT_MODEL` | `base.en` | `tiny.en`, `base.en`, `small.en`, `distil-small.en`, multilingual `base`/`small`, an HF repo id, or a local CTranslate2 directory. |
| `WISKRO_STT_COMPUTE_TYPE` | `int8` | CTranslate2 quantisation. `int8` is the fastest and leanest on CPU. |
| `WISKRO_STT_CPU_THREADS` | `2` | Threads per inference (`0` = all). |
| `WISKRO_STT_MAX_CONCURRENCY` | `1` | Parallel inferences. Keep at 1 on 2 cores. |
| `WISKRO_STT_BEAM_SIZE` | `1` | `1` = greedy (about 2× faster); `5` = slightly more accurate. |
| `WISKRO_STT_DEFAULT_LANGUAGE` | `en` | Used when a request omits `language`; empty = auto-detect. |
| `WISKRO_STT_MAX_UPLOAD_MB` | `25` | Upload size limit. |
| `WISKRO_STT_MAX_DURATION_SECONDS` | `600` | Decoded duration limit. |
| `WISKRO_STT_MIN_DURATION_SECONDS` | `0.3` | Noise gate: minimum clip length. |
| `WISKRO_STT_MIN_SPEECH_RMS` | `0.01` | Noise gate: speech-frame loudness (≈ -40 dBFS). |
| `WISKRO_STT_MIN_SNR_DB` | `10` | Noise gate: speech vs noise floor. Lower it for noisy environments. |
| `WISKRO_STT_MIN_SPEECH_BAND_RATIO` | `0.25` | Noise gate: share of energy in 300–3400 Hz. |

## Text-to-speech

| Variable | Default | Description |
| :--- | :--- | :--- |
| `WISKRO_TTS_MODEL_URL` / `_SHA256` | Kokoro v1.0 int8 (88 MB) | ONNX model and its pinned checksum. For the fp32 model (310 MB, more RAM), change both. |
| `WISKRO_TTS_VOICES_URL` / `_SHA256` | voices v1.0 (54 voices) | Voice pack and its pinned checksum. Empty checksum = skip verification. |
| `WISKRO_TTS_CPU_THREADS` | `2` | Threads per inference (`0` = all). |
| `WISKRO_TTS_MAX_CONCURRENCY` | `1` | Parallel inferences. |
| `WISKRO_TTS_DEFAULT_VOICE` | `am_adam` | Used when a request omits `voice`. |
| `WISKRO_TTS_MAX_CHARS` | `5000` | Per-request text limit. On the Core M, synthesis runs at roughly real time, so 5000 chars ≈ several minutes of compute. Lower it for snappy voice agents. |
| `WISKRO_TTS_MP3_BITRATE_KBPS` | `96` | 32–160 (MPEG-2 maximum at 24 kHz). |
| `WISKRO_TTS_CACHE_MB` | `32` | RAM for cached renders of repeated phrases (`0` = off). |
| `WISKRO_ESPEAK_LIBRARY` | auto | `libespeak-ng` path. Auto = system library (the Docker image installs `libespeak-ng1`), then the one bundled with `espeakng-loader`. |
| `WISKRO_ESPEAK_DATA_PATH` | auto | `espeak-ng-data` directory matching the library. |

## Sizing for the Haier server

Measured process memory (RSS) for the platform API with Wiskro, int8 models:

| State | RSS |
| :--- | :--- |
| Started, no model loaded | ~70–120 MB |
| Kokoro loaded | ~370 MB |
| Kokoro + Whisper `base.en` loaded | ~580 MB |
| After idle unload | back to baseline |

The container shares its memory limit with the web scraper's Chromium. With both modules enabled, raise the `platform-api` limit from `512m` to **`1536m`**. That still fits the 4 GB budget (other containers ≈ 1.3 GB + OS ≈ 0.4 GB), and the swap file from Appendix A of the server guide covers short spikes.

Recommended compose fragment:

```yaml
platform-api:
  volumes:
    - /mnt/dock-data/app-data/platform-api/models:/app/data/models
  environment:
    - ENABLE_WISKRO=true
    - WISKRO_IDLE_UNLOAD_SECONDS=900
    - WISKRO_STT_MODEL=base.en
  deploy:
    resources:
      limits:
        memory: 1536m
```

Tuning tips:

- **Faster STT, lower accuracy:** `WISKRO_STT_MODEL=tiny.en` (about half the memory and time of `base.en`).
- **Better STT accuracy:** `small.en` (~+400 MB, 2–3× slower). Only with `WISKRO_IDLE_UNLOAD_SECONDS` > 0.
- **Lowest first-request latency:** `WISKRO_PRELOAD=true` and `WISKRO_IDLE_UNLOAD_SECONDS=0`, at the cost of permanently resident models.
- **Two heavy users at once:** STT and TTS have independent slots, so one STT and one TTS can run in parallel; that already saturates both cores.
