"""Application settings loaded from the environment."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the standalone application."""

    data_dir: Path = Path("/data")
    host: str = "0.0.0.0"
    port: int = 8789
    log_level: str = "INFO"
    environment: str = "production"
    timezone: str = "UTC"
    scheduler_enabled: bool = False
    # Opt-in so a local validation or restart cannot unexpectedly scan or mail.
    worker_enabled: bool = False
    worker_poll_seconds: float = Field(default=0.5, gt=0, le=60)
    smtp_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_sender: str = "auction-watch@localhost"
    smtp_recipient: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    model_config = SettingsConfigDict(
        env_prefix="AW_",
        env_file=".env",
        extra="ignore",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
