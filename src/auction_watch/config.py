"""Application settings loaded from the environment."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the standalone application."""

    data_dir: Path = Path("/data")
    host: str = "0.0.0.0"
    port: int = 8789
    log_level: str = "INFO"
    environment: str = "production"

    model_config = SettingsConfigDict(
        env_prefix="AW_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
