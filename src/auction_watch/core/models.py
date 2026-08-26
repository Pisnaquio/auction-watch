"""Strict, immutable domain models shared by sources and the matcher."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit
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

from auction_watch.core.frozen import FrozenDict
from auction_watch.core.identity import decode_opportunity_key, encode_opportunity_key
from auction_watch.core.normalization import dedupe_terms, normalize_term
from auction_watch.core.validation import canonical_slug, external_id

_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_SEARCH_FIELDS = frozenset({"title", "description", "category"})
_VALID_REJECTIONS = frozenset(
    {
        "profile_disabled",
        "source_not_selected",
        "lot_inactive",
        "excluded_term",
        "missing_required_terms",
        "no_positive_trigger",
        "unknown_price",
        "price_above_maximum",
        "score_below_minimum",
    }
)

__all__ = [
    "AuctionGroup",
    "AuctionLot",
    "MatchResult",
    "PriceFilter",
    "SearchProfile",
    "SearchSchedule",
    "decode_opportunity_key",
    "encode_opportunity_key",
]


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    return cleaned


def _http_url(value: object, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an absolute HTTP or HTTPS URL")
    cleaned = value.strip()
    if optional and not cleaned:
        return None
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain credentials")
    return cleaned


def _currency(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CURRENCY.fullmatch(value) is None:
        raise ValueError("currency must be exactly three ASCII uppercase letters")
    return value


def _decimal_amount(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{label} must be a finite Decimal, not a float")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be a valid Decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return amount


def _ordered_strings(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, set)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list or tuple")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} values must be strings")
    return tuple(value)


class DomainModel(BaseModel):
    """Base settings shared by immutable, strict domain models."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AuctionGroup(DomainModel):
    source_id: str
    auction_id: str
    title: str
    url: str
    category: str = ""
    active: StrictBool
    closing_at: AwareDatetime | None = None
    observed_at: AwareDatetime

    _validate_source_id = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _validate_auction_id = field_validator("auction_id", mode="before")(
        lambda value: external_id(value, "auction_id")
    )
    _validate_title = field_validator("title", mode="before")(
        lambda value: _nonempty_text(value, "title")
    )
    _validate_url = field_validator("url", mode="before")(
        lambda value: _http_url(value, "url")
    )


class AuctionLot(DomainModel):
    source_id: str
    auction_id: str
    lot_id: str
    title: str
    description: str = ""
    category: str = ""
    price_value: Decimal | None = None
    price_currency: str | None = None
    price_label: str = ""
    closing_at: AwareDatetime | None = None
    lot_url: str
    auction_url: str
    image_url: str | None = None
    active: StrictBool
    observed_at: AwareDatetime

    _validate_source_id = field_validator("source_id", mode="before")(
        lambda value: canonical_slug(value, "source_id")
    )
    _validate_auction_id = field_validator("auction_id", mode="before")(
        lambda value: external_id(value, "auction_id")
    )
    _validate_lot_id = field_validator("lot_id", mode="before")(
        lambda value: external_id(value, "lot_id")
    )
    _validate_title = field_validator("title", mode="before")(
        lambda value: _nonempty_text(value, "title")
    )
    _validate_lot_url = field_validator("lot_url", mode="before")(
        lambda value: _http_url(value, "lot_url")
    )
    _validate_auction_url = field_validator("auction_url", mode="before")(
        lambda value: _http_url(value, "auction_url")
    )
    _validate_image_url = field_validator("image_url", mode="before")(
        lambda value: _http_url(value, "image_url", optional=True)
    )
    _validate_price_value = field_validator("price_value", mode="before")(
        lambda value: _decimal_amount(value, "price_value")
    )
    _validate_price_currency = field_validator("price_currency", mode="before")(_currency)

    @model_validator(mode="after")
    def validate_price_pair(self) -> AuctionLot:
        if (self.price_value is None) != (self.price_currency is None):
            raise ValueError("price_value and price_currency must be provided together")
        return self

    @property
    def opportunity_key(self) -> str:
        """Return the versioned reversible source/auction/lot identity."""

        return encode_opportunity_key(self.source_id, self.auction_id, self.lot_id)


class PriceFilter(DomainModel):
    maximum: Decimal | None = None
    currency: str | None = None
    on_unknown: Literal["include", "exclude"] = "include"

    _validate_maximum = field_validator("maximum", mode="before")(
        lambda value: _decimal_amount(value, "maximum")
    )
    _validate_currency = field_validator("currency", mode="before")(_currency)

    @field_validator("maximum")
    @classmethod
    def validate_maximum(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("maximum must be positive")
        return value

    @model_validator(mode="after")
    def validate_price_pair(self) -> PriceFilter:
        if (self.maximum is None) != (self.currency is None):
            raise ValueError("maximum and currency must be provided together")
        return self


class SearchSchedule(DomainModel):
    enabled: StrictBool = False
    times: tuple[str, ...] = ()
    timezone: str = "UTC"

    @field_validator("times", mode="before")
    @classmethod
    def normalize_times(cls, value: object) -> tuple[str, ...]:
        values = _ordered_strings(value, "times")
        normalized: list[str] = []
        for raw_time in values:
            if len(raw_time) != 5 or raw_time[2] != ":":
                raise ValueError("schedule times must use HH:MM")
            try:
                hour, minute = (int(part) for part in raw_time.split(":", 1))
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
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_enabled_schedule(self) -> SearchSchedule:
        if self.enabled and not self.times:
            raise ValueError("an enabled schedule requires at least one time")
        return self


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

    @field_validator("price_filter", mode="before")
    @classmethod
    def canonicalize_empty_price_filter(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, PriceFilter):
            return None if value.maximum is None and value.currency is None else value
        if isinstance(value, dict):
            allowed = {"maximum", "currency", "on_unknown"}
            if (
                set(value).issubset(allowed)
                and value.get("maximum") is None
                and value.get("currency") is None
            ):
                return None
        return value

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> str:
        return canonical_slug(value, "id")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _nonempty_text(value, "name")

    @field_validator(
        "keywords_any", "keywords_all", "exact_phrases", "exclude_keywords", mode="before"
    )
    @classmethod
    def normalize_keyword_lists(cls, value: object) -> tuple[str, ...]:
        values = _ordered_strings(value, "keyword list")
        return tuple(dedupe_terms(values))

    @field_validator("source_ids", mode="before")
    @classmethod
    def normalize_sources(cls, value: object) -> tuple[str, ...]:
        values = _ordered_strings(value, "source_ids")
        return tuple(
            dict.fromkeys(canonical_slug(source_id, "source_id") for source_id in values)
        )

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
            if not normalized:
                raise ValueError("boost keyword keys must not be empty")
            if isinstance(raw_weight, bool) or not isinstance(raw_weight, int):
                raise ValueError("boost weights must be integers")
            if not 0 < raw_weight <= 100:
                raise ValueError("boost weights must be between 1 and 100")
            if normalized not in normalized_keys:
                normalized_keys.add(normalized)
                result[key] = raw_weight
        return dict(sorted(result.items(), key=lambda item: normalize_term(item[0])))

    @field_validator("boost_keywords")
    @classmethod
    def freeze_boosts(cls, value: dict[str, StrictInt]) -> dict[str, StrictInt]:
        return FrozenDict(value)

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

        positive_groups = (self.keywords_any, self.keywords_all, self.exact_phrases)
        seen_positive: dict[str, str] = {}
        for group_name, terms in zip(
            ("keywords_any", "keywords_all", "exact_phrases"), positive_groups, strict=True
        ):
            for term in terms:
                normalized = normalize_term(term)
                if normalized in seen_positive:
                    raise ValueError(
                        f"positive rule {term!r} repeats {seen_positive[normalized]!r}"
                    )
                seen_positive[normalized] = group_name

        excluded = {normalize_term(term) for term in self.exclude_keywords}
        positive = set(seen_positive)
        if positive & excluded:
            raise ValueError("positive rules and exclusions must not overlap")
        boosts = {normalize_term(term) for term in self.boost_keywords}
        if boosts & excluded:
            raise ValueError("boosts and exclusions must not overlap")
        return self


class MatchResult(DomainModel):
    profile_id: str
    opportunity_key: str
    matched: StrictBool
    score: StrictInt = 0
    matched_terms: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    missing_required_terms: tuple[str, ...] = ()
    matched_fields: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    rejection_reasons: tuple[str, ...] = ()
    explanation: str

    _validate_profile_id = field_validator("profile_id", mode="before")(
        lambda value: canonical_slug(value, "profile_id")
    )

    @field_validator("opportunity_key", mode="before")
    @classmethod
    def validate_opportunity_key(cls, value: object) -> str:
        key = _nonempty_text(value, "opportunity_key")
        decode_opportunity_key(key)
        return key

    _validate_explanation = field_validator("explanation", mode="before")(
        lambda value: _nonempty_text(value, "explanation")
    )

    @field_validator("matched_terms", "excluded_terms", "missing_required_terms", mode="before")
    @classmethod
    def validate_ordered_terms(cls, value: object) -> tuple[str, ...]:
        values = _ordered_strings(value, "result terms")
        if any(not item.strip() for item in values):
            raise ValueError("result terms must not be empty")
        normalized = [normalize_term(item) for item in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("result terms must not contain duplicates")
        return tuple(values)

    @field_validator("matched_fields", mode="before")
    @classmethod
    def validate_matched_fields(cls, value: object) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, dict):
            raise ValueError("matched_fields must be an object")
        result: dict[str, tuple[str, ...]] = {}
        for term, fields in value.items():
            if not isinstance(term, str) or not term.strip():
                raise ValueError("matched_fields terms must not be empty")
            field_values = _ordered_strings(fields, "matched_fields values")
            if not field_values or any(field not in _SEARCH_FIELDS for field in field_values):
                raise ValueError("matched_fields contains an unsupported field")
            if len(field_values) != len(set(field_values)):
                raise ValueError("matched_fields values must not contain duplicates")
            result[term] = tuple(field_values)
        return result

    @field_validator("matched_fields")
    @classmethod
    def freeze_matched_fields(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        return FrozenDict(value)

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: int) -> int:
        if value < 0:
            raise ValueError("score must not be negative")
        return value

    @model_validator(mode="after")
    def validate_result_state(self) -> MatchResult:
        allowed_terms = set(self.matched_terms) | set(self.excluded_terms)
        if not set(self.matched_fields).issubset(allowed_terms):
            raise ValueError("matched_fields contains a term absent from the result terms")
        if self.matched:
            if not self.matched_terms:
                raise ValueError("a successful result must contain a matched term")
            if self.rejection_reasons or self.excluded_terms or self.missing_required_terms:
                raise ValueError("a successful result must not contain rejection details")
        elif not self.rejection_reasons or any(
            reason not in _VALID_REJECTIONS for reason in self.rejection_reasons
        ):
            raise ValueError("a rejected result must contain valid rejection codes")
        return self
