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
        'Mario Pereira Bengoa "El Rey" óleo sobre cartón',
        "Libro La guerra del fin del Mundo, Mario Vargas Llosa",
        "Portaretrato FAMILY nuevo",
        "Vagón de tren modelo Family Lines",
        "Caja porta C. Ds. forrada imitación lomo de libros",
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


@pytest.mark.parametrize(
    "text",
    [
        "Super Mario Bros cartucho",
        "Mario Kart Wii",
        "Family Game consola retro",
        "Nintendo DS Lite con juegos",
        "Juego DS",
        "Funda para Switch Lite",
    ],
)
def test_ambiguous_console_terms_require_and_accept_gaming_context(text: str) -> None:
    result = match_lot(consoles_profile(), lot(text))
    assert result.matched is True
    assert result.matched_terms


def test_mechanical_control_is_rejected_without_hiding_console_controls() -> None:
    mechanical = match_lot(
        consoles_profile(),
        lot(
            "Colección de piezas mecánicas/eléctricas",
            "Incluye consola de control industrial",
        ),
    )
    gaming = match_lot(
        consoles_profile(),
        lot("Control original para consola Nintendo"),
    )

    assert mechanical.matched is False
    assert gaming.matched is True


def test_risk_signals_reduce_score_without_hardcoding_matcher_category_logic() -> None:
    healthy = match_lot(consoles_profile(), lot("Consola Nintendo", "funcionando original"))
    risky = match_lot(consoles_profile(), lot("Consola Nintendo", "no prende sin cables"))

    assert healthy.matched is True
    assert risky.matched is True
    assert risky.score < healthy.score
