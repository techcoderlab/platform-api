# ─────────────────────────────────────────────────────
# Module   : app.core.config
# ─────────────────────────────────────────────────────
import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    SERVICE_NAME: str = Field(default="platform-api", description="Service identity")
    API_VERSION: str = Field(default="v1", description="API Version")
    API_PREFIX: str = Field(default="api", description="API Prefix")
    
    # Environment
    ENV: str = Field(default="production", description="Environment: dev, staging, production")
    
    # Security
    API_KEY: str = Field(default="change-me-in-production", description="Global API Key for ngrok protection")
    CORS_ORIGINS: List[str] = Field(
        default=["*"], 
        description="List of allowed CORS origins"
    )
    
    # Observability
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    LOG_FORMAT: str = Field(default="json", description="json or rich")
    
    # Concurrency
    MAX_WORKERS: int = Field(default=4, description="Max ThreadPoolExecutor workers")
    
    # Module: Image Factory
    MAX_ZIP_SIZE_MB: int = Field(default=50, description="Max total size of zip")
    ZIP_STORAGE: str = Field(default="/app/data/image_zips", description="Path for temp zip storage")
    ZIP_EXPIRES_IN: int = Field(default=600, description="Zip expiration in seconds (default 10m)")
    
    # Module: Web Scraper (Playwright)
    CHROMIUM_PATH: str = Field(default="/usr/bin/chromium", description="Path to system chromium")
    POOL_SIZE: int = Field(default=4, description="Max number of browsers in pool")
    MAX_ACTIVE_CONTEXTS: int = Field(default=20, description="Max number of browser sessions in total")
    BROWSER_SESSION_TTL: int = Field(default=300, description="Expiry time for each browser session")
    PROXY_LIST: str = Field(default="", description="Comma-separated manual proxy list")
    PROXY_ROTATING_URL: str = Field(default="", description="Rotating proxy URL")
    CB_FAILURE_THRESHOLD: int = Field(default=5, description="Circuit breaker failure threshold")
    CB_RECOVERY_TIMEOUT: int = Field(default=30, description="Circuit breaker recovery timeout in seconds")
    BACKOFF_MAX_ATTEMPTS: int = Field(default=5, description="Max retry attempts")
    BACKOFF_BASE_WAIT: float = Field(default=2.0, description="Exponential backoff initial wait")
    BACKOFF_MAX_WAIT: float = Field(default=60.0, description="Exponential backoff max wait cap")
    MAX_QUEUE_SIZE: int = Field(default=100, description="Max pending analysis tasks")
    WORKER_COUNT: int = Field(default=4, description="Number of background scraper workers")

    # Module: Wiskro (Whisper STT + Kokoro TTS, CPU-only ONNX/CTranslate2 inference)
    WISKRO_MODEL_DIR: str = Field(default="/app/data/models", description="Persistent cache for STT/TTS model files")
    WISKRO_ALLOW_DOWNLOAD: bool = Field(default=True, description="Download missing model files on demand")
    WISKRO_PRELOAD: bool = Field(default=False, description="Load models into RAM at startup instead of on first request")
    WISKRO_IDLE_UNLOAD_SECONDS: int = Field(default=900, ge=0, description="Unload an idle model after N seconds to free RAM (0 = never)")
    WISKRO_MAX_PENDING: int = Field(default=8, ge=0, description="Max requests waiting per engine before rejecting with 429")
    WISKRO_QUEUE_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0, description="Max wait for an inference slot before 503")

    WISKRO_STT_MODEL: str = Field(default="base.en", description="faster-whisper model size/HF repo id or local CTranslate2 directory")
    WISKRO_STT_COMPUTE_TYPE: str = Field(default="int8", description="CTranslate2 compute type (int8 is fastest on CPU)")
    WISKRO_STT_CPU_THREADS: int = Field(default=2, ge=0, description="Threads per STT inference (0 = all cores)")
    WISKRO_STT_MAX_CONCURRENCY: int = Field(default=1, ge=1, description="Concurrent STT inferences")
    WISKRO_STT_BEAM_SIZE: int = Field(default=1, ge=1, le=10, description="Beam size (1 = greedy, fastest)")
    WISKRO_STT_DEFAULT_LANGUAGE: str = Field(default="en", description="Default language code ('' = auto-detect)")
    WISKRO_STT_MAX_UPLOAD_MB: int = Field(default=25, ge=1, description="Max uploaded audio size")
    WISKRO_STT_MAX_DURATION_SECONDS: int = Field(default=600, ge=1, description="Max decoded audio duration")
    WISKRO_STT_MIN_DURATION_SECONDS: float = Field(default=0.3, ge=0, description="Noise gate: shorter clips are rejected")
    WISKRO_STT_MIN_SPEECH_RMS: float = Field(default=0.01, ge=0, description="Noise gate: min loudness of the speech frames")
    WISKRO_STT_MIN_SNR_DB: float = Field(default=10.0, description="Noise gate: min signal-to-noise ratio")
    WISKRO_STT_MIN_SPEECH_BAND_RATIO: float = Field(default=0.25, ge=0, le=1, description="Noise gate: min energy share in 300-3400 Hz")

    WISKRO_TTS_MODEL_URL: str = Field(
        default="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
        description="Kokoro ONNX model download URL",
    )
    WISKRO_TTS_MODEL_SHA256: str = Field(
        default="6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
        description="Expected SHA-256 of the model file ('' = skip verification)",
    )
    WISKRO_TTS_VOICES_URL: str = Field(
        default="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
        description="Kokoro voices pack download URL",
    )
    WISKRO_TTS_VOICES_SHA256: str = Field(
        default="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        description="Expected SHA-256 of the voices file ('' = skip verification)",
    )
    WISKRO_TTS_CPU_THREADS: int = Field(default=2, ge=0, description="Threads per TTS inference (0 = all cores)")
    WISKRO_TTS_MAX_CONCURRENCY: int = Field(default=1, ge=1, description="Concurrent TTS inferences")
    WISKRO_TTS_DEFAULT_VOICE: str = Field(default="am_adam", description="Voice used when a request names none")
    WISKRO_TTS_MAX_CHARS: int = Field(default=5000, ge=1, description="Max characters per TTS request")
    WISKRO_TTS_MP3_BITRATE_KBPS: int = Field(default=96, ge=32, le=160, description="MP3 bitrate (24 kHz MPEG-2 caps at 160)")
    WISKRO_TTS_CACHE_MB: int = Field(default=32, ge=0, description="In-memory cache of rendered audio (0 = disabled)")
    WISKRO_ESPEAK_LIBRARY: str = Field(default="", description="Path to libespeak-ng ('' = system library, then bundled)")
    WISKRO_ESPEAK_DATA_PATH: str = Field(default="", description="Path to espeak-ng-data ('' = auto-detect)")

    ENABLE_IMAGE_FACTORY: bool = Field(default=False, description="Enable image factory module")
    ENABLE_WEB_SCRAPER: bool = Field(default=False, description="Enable web scraper module")
    ENABLE_WISKRO: bool = Field(default=False, description="Enable wiskro speech (STT/TTS) module")

    @property
    def api_base(self) -> str:
        return f"/{self.API_PREFIX}/{self.API_VERSION}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

settings = Settings()

# Ensure temp data dir exists
os.makedirs(settings.ZIP_STORAGE, exist_ok=True)
