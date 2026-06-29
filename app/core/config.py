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
