"""
PCK Pack Manager — Application Configuration

Loads settings from environment variables with sensible defaults.
Never commit actual secrets — use .env locally.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Application settings loaded from environment."""

    # Gemini
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-3.8-flash")

    # App
    app_env: str = Field(default="development")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # Storage — keep DB + images under one root so a single volume survives restarts
    storage_root: str = Field(default="./storage")
    database_path: str = Field(default="./storage/pack_manager.db")
    database_url: str = Field(default="sqlite+aiosqlite:///./storage/pack_manager.db")
    image_storage_path: str = Field(default="./storage/images")

    # Security
    secret_key: str = Field(default="change-me-in-production")

    # Vision
    confidence_threshold: float = Field(default=0.5)
    image_quality_blur_threshold: float = Field(default=100.0)
    image_quality_brightness_min: float = Field(default=40.0)
    image_quality_brightness_max: float = Field(default=250.0)
    max_image_size_mb: int = Field(default=20)
    vlm_timeout_seconds: int = Field(default=60)
    vlm_max_retries: int = Field(default=2)
    vlm_retry_base_seconds: float = Field(default=1.0)


@lru_cache
def get_settings() -> Settings:
    """Load settings from environment variables."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        storage_root=os.getenv("STORAGE_ROOT", "./storage"),
        database_path=os.getenv(
            "DATABASE_PATH",
            os.getenv("STORAGE_ROOT", "./storage").rstrip("/\\") + "/pack_manager.db",
        ),
        database_url=os.getenv(
            "DATABASE_URL",
            "sqlite+aiosqlite:///"
            + os.getenv(
                "DATABASE_PATH",
                os.getenv("STORAGE_ROOT", "./storage").rstrip("/\\") + "/pack_manager.db",
            ).replace("\\", "/"),
        ),
        image_storage_path=os.getenv(
            "IMAGE_STORAGE_PATH",
            os.getenv("STORAGE_ROOT", "./storage").rstrip("/\\") + "/images",
        ),
        secret_key=os.getenv("SECRET_KEY", "change-me-in-production"),
        vlm_timeout_seconds=int(os.getenv("VLM_TIMEOUT_SECONDS", "60")),
        vlm_max_retries=int(os.getenv("VLM_MAX_RETRIES", "2")),
        vlm_retry_base_seconds=float(os.getenv("VLM_RETRY_BASE_SECONDS", "1.0")),
    )
