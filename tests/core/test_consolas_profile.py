from datetime import UTC, datetime

import pytest

from auction_watch.core.matching import match_lot
from auction_watch.core.models import AuctionLot
from auction_watch.profiles.seed import consoles_profile

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def lot(title: str, description: str = "") -> AuctionLot:
    return AuctionLot(
        source_id="remotes",
        auction_id="7544",
        lot_id=title,
        title=title,
        description=description,
        lot_url="https://example.test/lot",
        auction_url="https://example.test/auction",
        active=True,
        observed_at=NOW,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Nintendo Switch original",
        "Nintento con juegos",
        "Atari 2600 con joystick",
        "Pokemon y Zelda para Game Boy",
        "Consola Odyssey retro",
    ],
)
def test_consolas_portable_positive_vocabulary_matches(text: str) -> None:
    result = match_lot(consoles_profile(), lot(text))
    assert result.matched is True
    assert result.matched_terms


@pytest.mark.parametrize(
    "text",
    [
        "Network switch ethernet gigabit",
        "Control remoto universal",
        "Mesa de ping pong",
    ],
)
def test_consolas_context_rules_reject_historical_false_positives(text: str) -> None:
    result = match_lot(consoles_profile(), lot(text))
    assert result.matched is False


def test_switch_requires_no_network_context_but_matches_nintendo_switch() -> None:
    gaming = match_lot(consoles_profile(), lot("Nintendo Switch consola"))
    network = match_lot(consoles_profile(), lot("Switch de red ethernet"))

    assert gaming.matched is True
    assert "switch" in gaming.matched_terms
    assert "switch" not in network.matched_terms


def test_risk_signals_reduce_score_without_hardcoding_matcher_category_logic() -> None:
    healthy = match_lot(consoles_profile(), lot("Consola Nintendo", "funcionando original"))
    risky = match_lot(consoles_profile(), lot("Consola Nintendo", "no prende sin cables"))

    assert healthy.matched is True
    assert risky.matched is True
    assert risky.score < healthy.score
