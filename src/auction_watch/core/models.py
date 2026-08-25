"""Normalized domain models shared by sources and the matcher."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from auction_watch.core.normalization import dedupe_terms, normalize_term


def _required_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("identifier must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("identifier must not be empty")
    return cleaned


def _source_id(value: str) -> str:
    return _required_id(value).casefold()


def _currency(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("currency must be a string")
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned.upper()


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("decimal values must not be provided as float")
    return value


class DomainModel(BaseModel):
    """Base settings shared by immutable, strict domain models."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class AuctionGroup(DomainModel):
    source_id: str
    auction_id: str
    title: str
    url: str
    category: str
    active: StrictBool
    closing_at: AwareDatetime | None
    observed_at: AwareDatetime

    _validate_source_id = field_validator("source_id", mode="before")(_source_id)
    _validate_auction_id = field_validator("auction_id", mode="before")(_required_id)
    _validate_title = field_validator("title", mode="before")(_required_id)
    _validate_url = field_validator("url", mode="before")(_required_id)
    _validate_category = field_validator("category", mode="before")(_required_id)


class AuctionLot(DomainModel):
    source_id: str
    auction_id: str
    lot_id: str
    title: str
    description: str
    category: str
    price_value: Decimal | None
    price_currency: str | None
    price_label: str
    closing_at: AwareDatetime | None
    lot_url: str
    auction_url: str
    image_url: str | None
    active: StrictBool
    observed_at: AwareDatetime

    _validate_source_id = field_validator("source_id", mode="before")(_source_id)
    _validate_auction_id = field_validator("auction_id", mode="before")(_required_id)
    _validate_lot_id = field_validator("lot_id", mode="before")(_required_id)
    _validate_title = field_validator("title", mode="before")(_required_id)
    _validate_category = field_validator("category", mode="before")(_required_id)
    _validate_lot_url = field_validator("lot_url", mode="before")(_required_id)
    _validate_auction_url = field_validator("auction_url", mode="before")(_required_id)
    _validate_price_value = field_validator("price_value", mode="before")(_reject_float)
    _validate_price_currency = field_validator("price_currency", mode="before")(_currency)

    @property
    def opportunity_key(self) -> str:
        """Return the stable source/auction/lot identity string."""

        return f"{self.source_id}:{self.auction_id}:{self.lot_id}"


class PriceFilter(DomainModel):
    maximum: Decimal | None = None
    currency: str | None = None
    on_unknown: Literal["include", "exclude"] = "include"

    _validate_maximum = field_validator("maximum", mode="before")(_reject_float)
    _validate_currency = field_validator("currency", mode="before")(_currency)

    @field_validator("maximum")
    @classmethod
    def maximum_must_be_positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("maximum must be positive")
        return value

    @model_validator(mode="after")
    def maximum_requires_currency(self) -> PriceFilter:
        if self.maximum is not None and self.currency is None:
            raise ValueError("currency is required when maximum is set")
        return self


class SearchSchedule(DomainModel):
    enabled: StrictBool = False
    times: tuple[str, ...] = ()
    timezone: str = "UTC"

    @field_validator("times", mode="before")
    @classmethod
    def normalize_times(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ValueError("times must be a sequence of HH:MM values")
        normalized: list[str] = []
        for raw_time in value:
            if not isinstance(raw_time, str):
                raise ValueError("schedule times must be strings")
            time_value = raw_time.strip()
            if len(time_value) != 5 or time_value[2] != ":":
                raise ValueError("schedule times must use HH:MM")
            try:
                hour, minute = (int(part) for part in time_value.split(":", 1))
            except ValueError as exc:
                raise ValueError("schedule times must use HH:MM") from exc
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("schedule times must use a valid clock time")
            canonical = f"{hour:02d}:{minute:02d}"
            if canonical not in normalized:
                normalized.append(canonical)
        return tuple(normalized)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        timezone = value.strip()
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return timezone


class SearchProfile(DomainModel):
    id: str
    name: str
    enabled: StrictBool = True
    keywords_any: tuple[str, ...] = ()
    keywords_all: tuple[str, ...] = ()
    exact_phrases: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    boost_keywords: dict[str, StrictInt] = Field(default_factory=dict)
    source_ids: tuple[str, ...]
    minimum_score: StrictInt = 0
    price_filter: PriceFilter | None = None
    notification_mode: Literal["disabled", "matches", "matches_or_failure"] = "disabled"
    schedule: SearchSchedule = Field(default_factory=SearchSchedule)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        cleaned = value.strip().casefold()
        if not cleaned or any(part == "" for part in cleaned.split("-")):
            raise ValueError("id must be a non-empty slug")
        if any(not (char.isalnum() or char == "-") for char in cleaned):
            raise ValueError("id must be a non-empty slug")
        return cleaned

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        return " ".join(value.strip().split())

    @field_validator(
        "keywords_any", "keywords_all", "exact_phrases", "exclude_keywords", mode="before"
    )
    @classmethod
    def normalize_keyword_lists(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
            raise ValueError("keyword fields must be sequences")
        if any(not isinstance(item, str) for item in value):
            raise ValueError("keyword values must be strings")
        return tuple(dedupe_terms(value))

    @field_validator("source_ids", mode="before")
    @classmethod
    def normalize_sources(cls, value: object) -> tuple[str, ...]:
        if value is None or isinstance(value, str) or not isinstance(value, (list, tuple, set)):
            raise ValueError("source_ids must be a non-empty sequence")
        if any(not isinstance(item, str) for item in value):
            raise ValueError("source_ids values must be strings")
        return tuple(dedupe_terms(item.casefold() for item in value))

    @field_validator("boost_keywords", mode="before")
    @classmethod
    def normalize_boosts(cls, value: object) -> dict[str, int]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("boost_keywords must be a mapping")
        result: dict[str, int] = {}
        normalized_keys: set[str] = set()
        for raw_key, raw_weight in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("boost keyword keys must be strings")
            key = " ".join(raw_key.strip().split())
            normalized = normalize_term(key)
            if not normalized or normalized in normalized_keys:
                continue
            if isinstance(raw_weight, bool) or not isinstance(raw_weight, int):
                raise ValueError("boost weights must be integers")
            if not 0 < raw_weight <= 100:
                raise ValueError("boost weights must be between 1 and 100")
            normalized_keys.add(normalized)
            result[key] = raw_weight
        return result

    @field_validator("minimum_score")
    @classmethod
    def validate_minimum_score(cls, value: int) -> int:
        if value < 0:
            raise ValueError("minimum_score must not be negative")
        return value

    @model_validator(mode="after")
    def validate_profile_rules(self) -> SearchProfile:
        if not self.source_ids:
            raise ValueError("at least one source_id is required")
        if not (self.keywords_any or self.keywords_all or self.exact_phrases):
            raise ValueError("at least one positive matching rule is required")
        return self


class MatchResult(DomainModel):
    profile_id: str
    opportunity_key: str
    matched: bool
    score: int = 0
    matched_terms: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    missing_required_terms: tuple[str, ...] = ()
    matched_fields: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    rejection_reasons: tuple[str, ...] = ()
    explanation: str
