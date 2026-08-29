"""Pydantic contracts for the durable operational records.

These models deliberately live beside persistence rather than inside SQLAlchemy
models.  They are the boundary used by source orchestration, repositories and
future API code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from auction_watch.core.identity import decode_opportunity_key, encode_opportunity_key
from auction_watch.core.normalization import normalize_term
from auction_watch.core.validation import canonical_slug, external_id, http_url


class PersistenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _currency_code(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 3
        or not value.isascii()
        or not value.isupper()
        or not value.isalpha()
    ):
        raise ValueError("price_currency must be exactly three ASCII uppercase letters")
    return value


def _terms(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, set)) or not isinstance(value, (list, tuple)):
        raise ValueError("matched_terms must be a list or tuple")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("matched_terms must contain non-empty strings")
    normalized = [normalize_term(item) for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError("matched_terms must not contain duplicates")
    return tuple(item.strip() for item in value)


class SourceRecord(PersistenceModel):
    source_id: str
    label: str
    enabled: bool = True
    metadata: dict[str, object] = Field(default_factory=dict)

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )


class GroupRecord(PersistenceModel):
    source_id: str
    group_id: str
    title: str
    url: str
    category: str = ""
    active: bool = True
    closing_at: datetime | None = None
    observed_at: datetime

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _title = field_validator("title", mode="before")(lambda value: _text(value, "title"))
    _url = field_validator("url", mode="before")(lambda value: http_url(value, "url"))
    _group = field_validator("group_id", mode="before")(
        lambda value: external_id(value, "group_id")
    )
    _observed = field_validator("observed_at", mode="after")(utc_datetime)
    _closing = field_validator("closing_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )


class LotRecord(PersistenceModel):
    source_id: str
    auction_id: str
    lot_id: str
    title: str
    description: str = ""
    category: str = ""
    price_value: Decimal | None = None
    price_currency: str | None = None
    price_label: str = ""
    closing_at: datetime | None = None
    lot_url: str
    auction_url: str
    image_url: str | None = None
    active: bool = True
    observed_at: datetime

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _auction = field_validator("auction_id", mode="before")(
        lambda value: external_id(value, "auction_id")
    )
    _lot = field_validator("lot_id", mode="before")(lambda value: external_id(value, "lot_id"))
    _title = field_validator("title", mode="before")(lambda value: _text(value, "title"))
    _lot_url = field_validator("lot_url", mode="before")(lambda value: http_url(value, "lot_url"))
    _auction_url = field_validator("auction_url", mode="before")(
        lambda value: http_url(value, "auction_url")
    )
    _image_url = field_validator("image_url", mode="before")(
        lambda value: http_url(value, "image_url", optional=True)
    )
    _currency = field_validator("price_currency", mode="before")(
        lambda value: value if value is None else _currency_code(value)
    )
    _observed = field_validator("observed_at", mode="after")(utc_datetime)
    _closing = field_validator("closing_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )

    @model_validator(mode="after")
    def validate_price_pair(self) -> LotRecord:
        if (self.price_value is None) != (self.price_currency is None):
            raise ValueError("price_value and price_currency must be provided together")
        return self

    @property
    def opportunity_key(self) -> str:
        return encode_opportunity_key(self.source_id, self.auction_id, self.lot_id)


class RunRecord(PersistenceModel):
    run_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None

    _started = field_validator("started_at", mode="after")(utc_datetime)
    _finished = field_validator("finished_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )
    trigger: Literal["manual", "scheduled", "system"] = "manual"
    selected_sources: tuple[str, ...] = ()

    @field_validator("selected_sources", mode="before")
    @classmethod
    def validate_selected_sources(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, set)) or not isinstance(value, (list, tuple)):
            raise ValueError("selected_sources must be a list or tuple")
        values = tuple(canonical_slug(item, "source_id") for item in value)
        if len(values) != len(set(values)):
            raise ValueError("selected_sources must not contain duplicates")
        return values


class RunProfileRecord(PersistenceModel):
    run_id: str
    profile_id: str
    revision: int = Field(ge=1)
    position: int = Field(ge=0)

    _profile = field_validator("profile_id", mode="before")(
        lambda value: canonical_slug(value, "profile_id")
    )


class SourceRunRecord(PersistenceModel):
    run_id: str
    source_id: str
    status: Literal["pending", "running", "succeeded", "degraded", "failed"]
    discovered_count: int = Field(default=0, ge=0)
    processed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    inventory_authoritative: bool = False
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _started = field_validator("started_at", mode="after")(utc_datetime)
    _finished = field_validator("finished_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )


class CoverageReceipt(PersistenceModel):
    run_id: str
    source_id: str
    group_id: str
    status: Literal["complete", "partial", "failed"]
    inventory_authoritative: bool = False
    lot_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _group = field_validator("group_id", mode="before")(
        lambda value: external_id(value, "group_id")
    )
    _started = field_validator("started_at", mode="after")(utc_datetime)
    _finished = field_validator("finished_at", mode="after")(utc_datetime)


class OpportunityLifecycle(PersistenceModel):
    source_id: str
    auction_id: str
    lot_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    seen_count: int = Field(ge=1)
    active: bool = True
    removed_at: datetime | None = None
    last_present_run_id: str | None = None
    last_absence_run_id: str | None = None
    opportunity_key: str | None = None

    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _auction = field_validator("auction_id", mode="before")(
        lambda value: external_id(value, "auction_id")
    )
    _lot = field_validator("lot_id", mode="before")(lambda value: external_id(value, "lot_id"))
    _first = field_validator("first_seen_at", mode="after")(utc_datetime)
    _last = field_validator("last_seen_at", mode="after")(utc_datetime)
    _removed = field_validator("removed_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )

    @model_validator(mode="after")
    def validate_identity(self) -> OpportunityLifecycle:
        expected = encode_opportunity_key(self.source_id, self.auction_id, self.lot_id)
        if self.opportunity_key is not None:
            decode_opportunity_key(self.opportunity_key)
            if self.opportunity_key != expected:
                raise ValueError("opportunity_key does not match lifecycle identity")
        if self.active and self.removed_at is not None:
            raise ValueError("active lifecycle records cannot have removed_at")
        if not self.active and self.removed_at is None:
            raise ValueError("removed lifecycle records require removed_at")
        return self


class ProfileMatchRecord(PersistenceModel):
    profile_id: str
    source_id: str
    auction_id: str
    lot_id: str
    score: int = Field(ge=0)
    matched_terms: tuple[str, ...] = ()
    matched_fields: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    first_seen_at: datetime
    last_seen_at: datetime

    _profile = field_validator("profile_id", mode="before")(
        lambda value: canonical_slug(value, "profile_id")
    )
    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _auction = field_validator("auction_id", mode="before")(
        lambda value: external_id(value, "auction_id")
    )
    _lot = field_validator("lot_id", mode="before")(lambda value: external_id(value, "lot_id"))
    _first = field_validator("first_seen_at", mode="after")(utc_datetime)
    _last = field_validator("last_seen_at", mode="after")(utc_datetime)
    active: bool = True
    first_match_at: datetime | None = None
    last_match_at: datetime | None = None
    confirmed_match_run_id: str | None = None
    confirmed_absence_run_id: str | None = None

    _first_match = field_validator("first_match_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )
    _last_match = field_validator("last_match_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )
    _terms = field_validator("matched_terms", mode="before")(_terms)

    @field_validator("matched_fields", mode="before")
    @classmethod
    def validate_matched_fields(cls, value: object) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, dict):
            raise ValueError("matched_fields must be an object")
        result: dict[str, tuple[str, ...]] = {}
        for term, fields in value.items():
            if not isinstance(term, str) or not term.strip():
                raise ValueError("matched_fields terms must not be empty")
            if isinstance(fields, (str, set)) or not isinstance(fields, (list, tuple)):
                raise ValueError("matched_fields values must be a list or tuple")
            if not fields or any(
                field not in {"title", "description", "category"} for field in fields
            ):
                raise ValueError("matched_fields contains an unsupported field")
            if len(fields) != len(set(fields)):
                raise ValueError("matched_fields values must not contain duplicates")
            result[term.strip()] = tuple(fields)
        return result

    @model_validator(mode="after")
    def validate_match_fields(self) -> ProfileMatchRecord:
        if not set(self.matched_fields).issubset(self.matched_terms):
            raise ValueError("matched_fields contains a term absent from matched_terms")
        return self


class UserOpportunityState(PersistenceModel):
    profile_id: str
    source_id: str
    auction_id: str
    lot_id: str
    state: Literal["none", "following", "dismissed"] = "none"
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    _profile = field_validator("profile_id", mode="before")(
        lambda value: canonical_slug(value, "profile_id")
    )
    _source = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _auction = field_validator("auction_id", mode="before")(
        lambda value: external_id(value, "auction_id")
    )
    _lot = field_validator("lot_id", mode="before")(lambda value: external_id(value, "lot_id"))
    _created = field_validator("created_at", mode="after")(utc_datetime)
    _updated = field_validator("updated_at", mode="after")(utc_datetime)


class NotificationOutboxRecord(PersistenceModel):
    dedupe_key: str
    channel: str
    profile_id: str
    run_id: str | None = None
    snapshot_id: str | None = None
    status: Literal["pending", "sending", "sent", "failed", "uncertain"] = "pending"
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    _dedupe = field_validator("dedupe_key", mode="before")(lambda value: _text(value, "dedupe_key"))
    _channel = field_validator("channel", mode="before")(lambda value: _text(value, "channel"))
    _profile = field_validator("profile_id", mode="before")(
        lambda value: canonical_slug(value, "profile_id")
    )
    _created = field_validator("created_at", mode="after")(utc_datetime)
    _updated = field_validator("updated_at", mode="after")(utc_datetime)
    _next = field_validator("next_attempt_at", mode="after")(
        lambda value: utc_datetime(value) if value else None
    )
