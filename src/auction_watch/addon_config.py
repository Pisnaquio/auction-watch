"""Validated Home Assistant add-on options.

This module deliberately contains only declarative options.  It never logs the
values because some options, such as the SMTP password, are secrets.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AddonOptions(BaseModel):
    """The public, non-sensitive shape of ``/data/options.json``."""

    model_config = ConfigDict(extra="forbid")

    timezone: str = "UTC"
    scheduler_enabled: bool = False
    worker_poll_seconds: float = Field(default=0.5, gt=0, le=60)
    smtp_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_sender: str = "auction-watch@localhost"
    smtp_recipient: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    @field_validator(
        "smtp_host", "smtp_recipient", "smtp_username", "smtp_password", mode="before"
    )
    @classmethod
    def empty_strings_are_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_smtp(self) -> AddonOptions:
        if self.smtp_enabled and (not self.smtp_host or not self.smtp_recipient):
            raise ValueError("smtp_host and smtp_recipient are required when SMTP is enabled")
        return self
