from datetime import UTC, datetime
from decimal import Decimal

from auction_watch.core.matching import match_inventory, match_lot
from auction_watch.core.models import AuctionLot, PriceFilter
from auction_watch.profiles.models import SearchProfile

OBSERVED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def make_lot(**overrides: object) -> AuctionLot:
    values: dict[str, object] = {
        "source_id": "remates",
        "auction_id": "7691",
        "lot_id": "12",
        "title": "Vinilo de rock argentino",
        "description": "Spinetta y edición cuidada",
        "category": "Música",
        "price_value": Decimal("960"),
        "price_currency": "UYU",
        "price_label": "$ 960",
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


def test_keywords_any_and_title_bonus_are_deterministic() -> None:
    result = match_lot(make_profile(), make_lot())

    assert result.matched is True
    assert result.score == 3
    assert result.matched_terms == ("vinilo",)
    assert result.matched_fields["vinilo"] == ("title",)
    assert "vinilo" in result.explanation


def test_keywords_all_require_every_term() -> None:
    profile = make_profile(keywords_any=[], keywords_all=["vinilo", "primera edición"])
    result = match_lot(profile, make_lot(description="Sólo vinilo disponible"))

    assert result.matched is False
    assert result.missing_required_terms == ("primera edición",)
    assert result.rejection_reasons == ("missing_required_terms",)


def test_all_keywords_are_a_positive_trigger_when_they_are_the_only_rule() -> None:
    profile = make_profile(keywords_any=[], keywords_all=["vinilo", "rock"])
    result = match_lot(profile, make_lot())

    assert result.matched is True
    assert result.score == 8


def test_exact_phrase_requires_order_and_is_scored_once() -> None:
    profile = make_profile(keywords_any=[], exact_phrases=["rock argentino"])
    result = match_lot(profile, make_lot())
    reversed_result = match_lot(profile, make_lot(title="Argentino rock", description=""))

    assert result.matched is True
    assert result.score == 6
    assert reversed_result.matched is False
    assert reversed_result.rejection_reasons == ("no_positive_trigger",)


def test_exclusion_has_priority_over_positive_rules() -> None:
    profile = make_profile(exclude_keywords=["réplica"], boost_keywords={"spinetta": 10})
    result = match_lot(profile, make_lot(description="Réplica decorativa de Spinetta"))

    assert result.matched is False
    assert result.score == 0
    assert result.excluded_terms == ("réplica",)
    assert result.rejection_reasons == ("excluded_term",)
    assert "réplica" in result.explanation


def test_boost_increases_score_but_cannot_trigger_a_match_alone() -> None:
    profile = make_profile(keywords_any=["vinilo"], boost_keywords={"spinetta": 10})
    result = match_lot(profile, make_lot(title="Spinetta", description=""))

    assert result.matched is False
    assert result.score == 0
    assert result.rejection_reasons == ("no_positive_trigger",)


def test_category_filter_rejects_lots_outside_selected_categories() -> None:
    profile = make_profile(categories=["Música"])
    included = match_lot(profile, make_lot(category="Música y cultura"))
    excluded = match_lot(profile, make_lot(category="Decoración"))

    assert included.matched is True
    assert excluded.matched is False
    assert excluded.rejection_reasons == ("category_not_selected",)


def test_boost_applies_once_when_a_positive_rule_already_matches() -> None:
    profile = make_profile(boost_keywords={"spinetta": 10})
    result = match_lot(profile, make_lot())

    assert result.matched is True
    assert result.score == 13
    assert result.matched_terms == ("vinilo", "spinetta")


def test_score_minimum_rejects_below_threshold() -> None:
    result = match_lot(make_profile(minimum_score=4), make_lot())

    assert result.matched is False
    assert result.score == 3
    assert result.rejection_reasons == ("score_below_minimum",)


def test_profile_disabled_source_and_inactive_lot_are_rejected() -> None:
    disabled = match_lot(make_profile(enabled=False), make_lot())
    assert disabled.rejection_reasons == ("profile_disabled",)
    assert match_lot(make_profile(source_ids=["otra-fuente"]), make_lot()).rejection_reasons == (
        "source_not_selected",
    )
    assert match_lot(make_profile(), make_lot(active=False)).rejection_reasons == ("lot_inactive",)


def test_price_maximum_unknown_price_and_currency_policies() -> None:
    maximum = PriceFilter(maximum=Decimal("900"), currency="UYU")
    too_expensive = match_lot(make_profile(price_filter=maximum), make_lot())
    assert too_expensive.matched is False
    assert too_expensive.score == 3
    assert too_expensive.rejection_reasons == ("price_above_maximum",)

    include_unknown = match_lot(
        make_profile(price_filter=PriceFilter(maximum=Decimal("900"), currency="UYU")),
        make_lot(price_value=None, price_currency=None),
    )
    assert include_unknown.matched is True

    exclude_unknown = match_lot(
        make_profile(
            price_filter=PriceFilter(maximum=Decimal("900"), currency="UYU", on_unknown="exclude")
        ),
        make_lot(price_value=None, price_currency=None),
    )
    assert exclude_unknown.rejection_reasons == ("unknown_price",)

    different_currency = match_lot(
        make_profile(
            price_filter=PriceFilter(maximum=Decimal("900"), currency="USD", on_unknown="exclude")
        ),
        make_lot(),
    )
    assert different_currency.rejection_reasons == ("unknown_price",)


def test_inventory_keeps_rejections_and_preserves_input_order() -> None:
    first = make_lot(lot_id="1", title="Vinilo y cámaras")
    second = make_lot(lot_id="2", title="Libros antiguos", description="")
    profiles = [
        make_profile(id="vinilos", name="Vinilos"),
        make_profile(
            id="libros",
            name="Libros",
            keywords_any=["libros"],
            source_ids=["remates"],
        ),
    ]

    results = match_inventory(profiles, [first, second])

    assert [(result.profile_id, result.opportunity_key) for result in results] == [
        ("vinilos", "aw1:remates:7691:1"),
        ("vinilos", "aw1:remates:7691:2"),
        ("libros", "aw1:remates:7691:1"),
        ("libros", "aw1:remates:7691:2"),
    ]
    assert [result.matched for result in results] == [True, False, False, True]


def test_neutral_profiles_are_independent_and_do_not_mutate_inputs() -> None:
    lot = make_lot(title="Cámaras analógicas", description="Herramientas de precisión")
    profile = make_profile(id="camara", name="Cámaras", keywords_any=["cámaras"])
    original_lot = lot.model_dump()
    original_profile = profile.model_dump()

    result = match_lot(profile, lot)

    assert result.matched is True
    assert lot.model_dump() == original_lot
    assert profile.model_dump() == original_profile
