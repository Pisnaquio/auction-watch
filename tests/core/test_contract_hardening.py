from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from auction_watch.core.identity import decode_opportunity_key, encode_opportunity_key
from auction_watch.core.models import AuctionLot, MatchResult, PriceFilter
from auction_watch.profiles.models import SearchProfile
from auction_watch.sources.contracts import DecoderDiagnostic

OBSERVED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def make_lot(**overrides: object) -> AuctionLot:
    values: dict[str, object] = {
        "source_id": "remates",
        "auction_id": "group:7691",
        "lot_id": "lot:12",
        "title": "Vinilo",
        "description": "Descripción",
        "category": "",
        "price_value": Decimal("10"),
        "price_currency": "UYU",
        "price_label": "$ 10",
        "closing_at": OBSERVED_AT,
        "lot_url": "https://example.test/lots/12",
        "auction_url": "https://example.test/auctions/7691",
        "image_url": " ",
        "active": True,
        "observed_at": OBSERVED_AT,
    }
    values.update(overrides)
    return AuctionLot(**values)


def make_profile(**overrides: object) -> SearchProfile:
    values: dict[str, object] = {
        "id": "vinilos",
        "name": "Vinilos",
        "keywords_any": ["vinilo"],
        "source_ids": ["remates"],
    }
    values.update(overrides)
    return SearchProfile(**values)


def test_opportunity_key_is_versioned_reversible_and_collision_free() -> None:
    first = encode_opportunity_key("a", "b:c", "d")
    second = encode_opportunity_key("a", "b", "c:d")

    assert first.startswith("aw1:")
    assert first != second
    assert decode_opportunity_key(first) == ("a", "b:c", "d")
    assert decode_opportunity_key(second) == ("a", "b", "c:d")
    assert make_lot().opportunity_key == encode_opportunity_key("remates", "group:7691", "lot:12")


@pytest.mark.parametrize("key", ["", "aw2:a:b:c", "aw1:a:b", "aw1:a:b:c:d", "aw1:a:%:c"])
def test_malformed_opportunity_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError):
        decode_opportunity_key(key)


def test_non_canonical_and_invalid_utf8_keys_are_rejected() -> None:
    for key in ("aw1:%61:b:c", "aw1:a:%2f:c", "aw1:a:%FF:c"):
        with pytest.raises(ValueError):
            decode_opportunity_key(key)


def test_external_identity_components_round_trip_reserved_chars_and_unicode() -> None:
    key = encode_opportunity_key("remates", "remate / edición:1", "lote: 東京")

    assert decode_opportunity_key(key) == ("remates", "remate / edición:1", "lote: 東京")


def test_internal_slugs_are_ascii_lowercase_and_sources_dedupe_exactly() -> None:
    profile = make_profile(source_ids=["bavastro", "castells", "bavastro"])
    assert profile.source_ids == ("bavastro", "castells")
    for _field, overrides in (
        ("id", {"id": "José"}),
        ("id", {"id": "with_space"}),
        ("source_id", {"source_ids": ["Bavastro"]}),
    ):
        with pytest.raises(ValidationError):
            make_profile(**overrides)


def test_ordered_fields_reject_sets() -> None:
    with pytest.raises(ValidationError):
        make_profile(keywords_any={"vinilo"})
    with pytest.raises(ValidationError):
        make_profile(source_ids={"remates"})
    with pytest.raises(ValidationError):
        make_profile(schedule={"times": {"09:15"}})


@pytest.mark.parametrize(
    "fingerprint",
    (
        "root=object[token]",
        "root=object[recipient]",
        "https://example.test/payload",
        "root=object[user@example.test]",
    ),
)
def test_decoder_diagnostic_rejects_secret_bearing_or_non_structural_text(
    fingerprint: str,
) -> None:
    with pytest.raises(ValidationError):
        DecoderDiagnostic(
            group_id="group-1",
            status="shadow_only",
            category="structure_drift",
            confidence="low",
            fingerprint=fingerprint,
        )


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "file:///tmp/a", "data:text/plain,x", "/relative"]
)
def test_unsafe_or_relative_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        make_lot(lot_url=url)


def test_url_credentials_are_rejected_and_blank_image_is_none() -> None:
    with pytest.raises(ValidationError):
        make_lot(auction_url="https://user:password@example.test/auction")
    assert make_lot().image_url is None


@pytest.mark.parametrize("amount", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_amounts_must_be_finite_and_non_negative(amount: Decimal) -> None:
    with pytest.raises(ValidationError):
        make_lot(price_value=amount)
    with pytest.raises(ValidationError):
        PriceFilter(maximum=amount, currency="UYU")


@pytest.mark.parametrize("currency", ["uyu", "US", "US$", "１２３"])
def test_currencies_must_be_three_ascii_uppercase_letters(currency: str) -> None:
    with pytest.raises(ValidationError):
        make_lot(price_currency=currency)


def test_price_value_and_currency_are_a_coherent_pair() -> None:
    with pytest.raises(ValidationError):
        make_lot(price_value=Decimal("10"), price_currency=None)
    with pytest.raises(ValidationError):
        make_lot(price_value=None, price_currency="UYU")
    with pytest.raises(ValidationError):
        PriceFilter(maximum=None, currency="UYU")


def test_positive_rule_conflicts_and_boost_rules() -> None:
    with pytest.raises(ValidationError):
        make_profile(keywords_any=["José"], keywords_all=["jose"])
    with pytest.raises(ValidationError):
        make_profile(keywords_any=["vinilo"], exclude_keywords=["VINILO"])
    with pytest.raises(ValidationError):
        make_profile(
            keywords_any=["vinilo"],
            exclude_keywords=["réplica"],
            boost_keywords={"réplica": 4},
        )
    assert make_profile(keywords_any=["vinilo"], boost_keywords={"vinilo": 4}).boost_keywords


def test_enabled_schedule_requires_time_but_disabled_schedule_can_keep_times() -> None:
    with pytest.raises(ValidationError):
        make_profile(schedule={"enabled": True, "times": [], "timezone": "UTC"})
    profile = make_profile(schedule={"enabled": False, "times": ["09:15"], "timezone": "UTC"})
    assert profile.schedule.times == ("09:15",)


def test_mappings_are_deeply_immutable_and_json_is_deterministic() -> None:
    first = make_profile(boost_keywords={"zeta": 2, "alpha": 1})
    second = make_profile(boost_keywords={"alpha": 1, "zeta": 2})
    result = MatchResult(
        profile_id=first.id,
        opportunity_key=make_lot().opportunity_key,
        matched=True,
        matched_terms=("vinilo",),
        matched_fields={"vinilo": ("title",)},
        explanation="Coincidió.",
    )

    assert first.model_dump_json() == second.model_dump_json()
    with pytest.raises(TypeError):
        first.boost_keywords["nuevo"] = 3  # type: ignore[index]
    with pytest.raises(TypeError):
        result.matched_fields["vinilo"] = ("description",)  # type: ignore[index]


def test_match_result_states_and_fields_are_validated() -> None:
    valid_key = make_lot().opportunity_key
    with pytest.raises(ValidationError):
        MatchResult(profile_id="p", opportunity_key=valid_key, matched=1, explanation="x")
    with pytest.raises(ValidationError):
        MatchResult(
            profile_id="not a slug", opportunity_key=valid_key, matched=False, explanation="x"
        )
    with pytest.raises(ValidationError):
        MatchResult(profile_id="p", opportunity_key="not-an-aw-key", matched=False, explanation="x")
    with pytest.raises(ValidationError):
        MatchResult(profile_id="p", opportunity_key="aw1:%61:b:c", matched=False, explanation="x")
    with pytest.raises(ValidationError):
        MatchResult(profile_id="p", opportunity_key=valid_key, matched=False, explanation="x")
    with pytest.raises(ValidationError):
        MatchResult(
            profile_id="p",
            opportunity_key=valid_key,
            matched=True,
            excluded_terms=("réplica",),
            explanation="x",
        )
    with pytest.raises(ValidationError):
        MatchResult(
            profile_id="p",
            opportunity_key=valid_key,
            matched=False,
            rejection_reasons=("not-a-code",),
            explanation="x",
        )
    with pytest.raises(ValidationError):
        MatchResult(
            profile_id="p",
            opportunity_key=valid_key,
            matched=True,
            matched_fields={"vinilo": ("url",)},
            explanation="x",
        )
    with pytest.raises(ValidationError):
        MatchResult(
            profile_id="p",
            opportunity_key=valid_key,
            matched=True,
            matched_terms=("vinilo",),
            matched_fields={"otro": ("title",)},
            explanation="x",
        )
    with pytest.raises(ValidationError):
        MatchResult(
            profile_id="p",
            opportunity_key=valid_key,
            matched=True,
            matched_terms=("vinilo",),
            matched_fields={"vinilo": ("title", "title")},
            explanation="x",
        )
    with pytest.raises(ValidationError):
        MatchResult(
            profile_id="p",
            opportunity_key=valid_key,
            matched=True,
            matched_terms=("vinilo", "VÍNILO"),
            explanation="x",
        )
