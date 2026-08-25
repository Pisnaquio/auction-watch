from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from auction_watch.core.models import AuctionGroup, AuctionLot, MatchResult, PriceFilter
from auction_watch.profiles.models import SearchProfile

OBSERVED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def make_lot(**overrides: object) -> AuctionLot:
    values: dict[str, object] = {
        "source_id": "remates",
        "auction_id": "7691",
        "lot_id": "12",
        "title": "Vinilo de rock argentino",
        "description": "Edición cuidada de colección",
        "category": "Música",
        "price_value": Decimal("960.10"),
        "price_currency": "uyu",
        "price_label": "$ 960,10",
        "closing_at": OBSERVED_AT,
        "lot_url": "https://example.test/lots/12",
        "auction_url": "https://example.test/auctions/7691",
        "image_url": None,
        "active": True,
        "observed_at": OBSERVED_AT,
    }
    values.update(overrides)
    return AuctionLot(**values)


def make_profile(**overrides: object) -> SearchProfile:
    values: dict[str, object] = {
        "id": "vinilos-rock",
        "name": "Vinilos de rock",
        "keywords_any": ["vinilo"],
        "source_ids": ["remates"],
    }
    values.update(overrides)
    return SearchProfile(**values)


def test_auction_group_and_lot_serialize_normalized_contracts() -> None:
    group = AuctionGroup(
        source_id=" REMATES ",
        auction_id="7691",
        title="Remate de agosto",
        url="https://example.test/auctions/7691",
        category="General",
        active=True,
        closing_at=OBSERVED_AT,
        observed_at=OBSERVED_AT,
    )
    lot = make_lot()

    assert group.source_id == "remates"
    assert lot.price_currency == "UYU"
    assert lot.model_dump()["price_value"] == Decimal("960.10")
    assert lot.opportunity_key == "remates:7691:12"
    assert lot.model_dump_json()


def test_money_rejects_float_to_preserve_decimal_precision() -> None:
    with pytest.raises(ValidationError):
        make_lot(price_value=960.1)
    with pytest.raises(ValidationError):
        PriceFilter(maximum=1000.0, currency="UYU")


def test_datetime_without_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_lot(observed_at=datetime(2026, 8, 25, 12, 0))


def test_ids_and_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_lot(lot_id=" ")
    with pytest.raises(ValidationError):
        make_lot(unexpected="private-payload")


def test_price_filter_requires_positive_maximum_and_currency() -> None:
    assert PriceFilter(maximum=Decimal("1000"), currency="uyu").currency == "UYU"
    with pytest.raises(ValidationError):
        PriceFilter(maximum=Decimal("0"), currency="UYU")
    with pytest.raises(ValidationError):
        PriceFilter(maximum=Decimal("1000"))


def test_profile_normalizes_terms_and_schedule_without_duplicate_times() -> None:
    profile = make_profile(
        keywords_any=[" José ", "jose", ""],
        keywords_all=["edición"],
        exact_phrases=["rock argentino"],
        exclude_keywords=["Réplica", "replica"],
        boost_keywords={"Spinetta": 10},
        schedule={
            "enabled": True,
            "times": ["09:15", "09:15", "17:10"],
            "timezone": "America/Montevideo",
        },
    )

    assert profile.keywords_any == ("José",)
    assert profile.exclude_keywords == ("Réplica",)
    assert profile.boost_keywords == {"Spinetta": 10}
    assert profile.schedule.times == ("09:15", "17:10")


def test_profile_requires_positive_rule_and_valid_timezone() -> None:
    with pytest.raises(ValidationError):
        make_profile(keywords_any=[], keywords_all=[], exact_phrases=[])
    with pytest.raises(ValidationError):
        make_profile(schedule={"timezone": "Not/A_Timezone"})


def test_profile_requires_source_and_slug_id() -> None:
    with pytest.raises(ValidationError):
        make_profile(source_ids=[])
    with pytest.raises(ValidationError):
        make_profile(id="profile with spaces")


def test_models_are_immutable() -> None:
    lot = make_lot()
    profile = make_profile()
    result = MatchResult(
        profile_id=profile.id,
        opportunity_key=lot.opportunity_key,
        matched=True,
        explanation="Coincidió.",
    )

    with pytest.raises(ValidationError):
        lot.title = "otro"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        profile.name = "otro"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.score = 99  # type: ignore[misc]
