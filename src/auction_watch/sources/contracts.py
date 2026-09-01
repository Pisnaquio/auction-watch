"""Generic source contracts independent from profiles and categories."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.core.validation import canonical_slug, external_id

_STRUCTURAL_TEXT = re.compile(r"[A-Za-z0-9_$.,;=\[\]{}:-]+\Z")
_SENSITIVE_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "email",
    "password",
    "recipient",
    "secret",
    "smtp",
    "token",
    "username",
)


class SourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GroupReceipt(SourceContract):
    group_id: str
    status: Literal["complete", "partial", "failed"]
    inventory_authoritative: bool = False
    lot_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime

    _group = field_validator("group_id", mode="before")(
        lambda value: external_id(value, "group_id")
    )

    @field_validator("started_at", "finished_at", mode="after")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SkippedGroup(SourceContract):
    """A discovered group intentionally excluded before fetching its inventory."""

    group_id: str
    title: str
    status: Literal["skipped_irrelevant"] = "skipped_irrelevant"
    reason: Literal["art_title"] = "art_title"

    _group = field_validator("group_id", mode="before")(
        lambda value: external_id(value, "group_id")
    )

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("skipped group title must not be empty")
        return cleaned


class DecoderDiagnostic(SourceContract):
    """Sanitized structural evidence from a bounded adaptive decoder."""

    group_id: str
    status: Literal["adaptive_recovered", "shadow_only"]
    category: Literal[
        "envelope_drift",
        "html_response",
        "error_payload",
        "ambiguous_envelope",
        "unverified_empty",
        "lot_shape_drift",
        "structure_drift",
    ]
    confidence: Literal["high", "medium", "low"]
    path: str | None = None
    fingerprint: str

    _group = field_validator("group_id", mode="before")(
        lambda value: external_id(value, "group_id")
    )

    @field_validator("path", "fingerprint")
    @classmethod
    def bounded_structural_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("decoder diagnostic text must not be empty")
        if len(cleaned) > 500:
            raise ValueError("decoder diagnostic text is too long")
        lowered = cleaned.casefold()
        if _STRUCTURAL_TEXT.fullmatch(cleaned) is None or any(
            fragment in lowered for fragment in _SENSITIVE_FRAGMENTS
        ):
            raise ValueError("decoder diagnostic must contain structural metadata only")
        return cleaned


class SourceScanResult(SourceContract):
    source_id: str
    label: str
    groups: tuple[AuctionGroup, ...] = ()
    lots: tuple[AuctionLot, ...] = ()
    discovery_status: Literal["complete", "partial", "failed"]
    inventory_authoritative: bool = False
    # ``None`` preserves the legacy rule: source authority also authorizes
    # omissions. Volatile discovery feeds can opt out explicitly with False.
    omission_authoritative: bool | None = None
    receipts: tuple[GroupReceipt, ...] = ()
    skipped_groups: tuple[SkippedGroup, ...] = ()
    diagnostics: tuple[DecoderDiagnostic, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )


__all__ = ["DecoderDiagnostic", "GroupReceipt", "SkippedGroup", "SourceScanResult"]
