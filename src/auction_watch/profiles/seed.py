"""Versioned, public system-profile seeds."""

from __future__ import annotations

from auction_watch.core.models import SearchProfile

CONSOLAS_SEED_KEY = "auction-watch-consolas"
CONSOLAS_SEED_VERSION = 1


def consoles_profile() -> SearchProfile:
    """Return a fresh copy of the public console-oriented system profile."""

    return SearchProfile(
        id="consolas",
        name="Auction Watch Consolas",
        kind="system",
        locked=True,
        seed_key=CONSOLAS_SEED_KEY,
        seed_version=CONSOLAS_SEED_VERSION,
        enabled=True,
        keywords_any=(
            "consola",
            "videojuego",
            "playstation",
            "nintendo",
            "sega",
            "atari",
            "xbox",
            "game boy",
            "gameboy",
            "control",
            "joystick",
            "cartucho",
            "accesorio",
            "adaptador",
        ),
        keywords_all=(),
        exact_phrases=(),
        exclude_keywords=("impresora", "tinta", "router", "industrial"),
        boost_keywords={"colección": 5, "original": 3, "completo": 3},
        source_ids=("bavastro", "castells", "remotes", "todoremates", "prado"),
        minimum_score=0,
        notification_mode="disabled",
    )


def clone_as_user(
    profile: SearchProfile, profile_id: str, name: str | None = None
) -> SearchProfile:
    """Clone a system or user profile into a completely editable user profile."""

    values = profile.model_dump()
    values.update(
        {
            "id": profile_id,
            "name": name or f"Copia de {profile.name}",
            "kind": "user",
            "locked": False,
            "seed_key": None,
            "seed_version": 0,
        }
    )
    return SearchProfile(**values)
