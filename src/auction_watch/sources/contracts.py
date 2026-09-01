"""Generic source contracts independent from profiles and categories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.core.validation import canonical_slug, external_id


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


class SourceScanResult(SourceContract):
    source_id: str
    label: str
    groups: tuple[AuctionGroup, ...] = ()
    lots: tuple[AuctionLot, ...] = ()
    discovery_status: Literal["complete", "partial", "failed"]
    inventory_authoritative: bool = False
    receipts: tuple[GroupReceipt, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )


__all__ = ["GroupReceipt", "SourceScanResult"]
