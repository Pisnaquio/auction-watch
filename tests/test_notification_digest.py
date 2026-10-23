from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from auction_watch.async_ops import NotificationRepository
from auction_watch.core.identity import encode_opportunity_key
from auction_watch.core.models import SearchProfile
from auction_watch.notifications.rendering import render_matches_notification
from auction_watch.notifications.sender import FakeNotificationSender
from auction_watch.notifications.service import NotificationPlanner
from auction_watch.persistence.contracts import RunRecord
from auction_watch.persistence.database import Database
from auction_watch.persistence.migrations import upgrade_head
from auction_watch.persistence.operational_repository import OperationalRepository
from auction_watch.persistence.repository import ProfileRepository
from auction_watch.runner import RunOutcome
from auction_watch.worker import NotificationDeliveryWorker

# 18:00 in Montevideo: a mail rendered in UTC would move half the lots to the
# wrong day, which is exactly the mistake the digest must not make.
NOW = datetime(2026, 10, 23, 21, 0, tzinfo=UTC)
ZONE = "America/Montevideo"


def match(
    *,
    lot_id: str = "lot-1",
    title: str = "Colección de libros antiguos",
    closing_at: datetime | None = NOW + timedelta(hours=2),
    score: int = 7,
    terms: tuple[str, ...] = ("libro", "antiguo"),
    price_label: str = "$ 1.200",
    image_url: str | None = "https://example.test/lot-1.jpg",
    change: str | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "opportunity_key": encode_opportunity_key("bavastro", "auction-1", lot_id),
        "score": score,
        "matched_terms": list(terms),
        "lot": {
            "source_id": "bavastro",
            "auction_id": "auction-1",
            "lot_id": lot_id,
            "title": title,
            "price_label": price_label,
            "closing_at": closing_at.isoformat() if closing_at else None,
            "lot_url": f"https://example.test/{lot_id}",
            "auction_url": "https://example.test/auction-1",
            "image_url": image_url,
        },
    }
    if change:
        item["change"] = change
    return item


def render(matches: list[dict[str, object]]):
    return render_matches_notification(
        profile_name="Libros antiguos",
        matches=matches,
        generated_at=NOW,
        timezone=ZONE,
    )


def test_every_lot_is_visible_in_both_bodies() -> None:
    rendered = render([match()])

    assert rendered.subject == "Auction Watch · 1 lote para mirar en Libros antiguos"
    for body in (rendered.text, rendered.html):
        assert "Colección de libros antiguos" in body
        assert "https://example.test/lot-1" in body
        assert "Bavastro" in body
        assert "$ 1.200" in body
        assert "Cierra hoy 20:00" in body
        assert "puntaje 7" in body
        assert "coincide: libro, antiguo" in body
    assert 'src="https://example.test/lot-1.jpg"' in rendered.html
    assert rendered.html.startswith("<!doctype html>")


def test_lots_are_grouped_by_closing_day_in_the_configured_timezone() -> None:
    rendered = render(
        [
            match(lot_id="later", closing_at=NOW + timedelta(days=4)),
            match(lot_id="tomorrow", closing_at=NOW + timedelta(days=1)),
            match(lot_id="today", closing_at=NOW + timedelta(hours=1)),
            match(lot_id="undated", closing_at=None),
            match(lot_id="closed", closing_at=NOW - timedelta(days=2)),
        ]
    )

    headings = [
        line
        for line in rendered.text.splitlines()
        if line and not line.startswith((" ", "·", "Auction Watch", "Revisado", "Abrí"))
    ]
    assert headings == [
        "Cierran hoy (1)",
        "Cierran mañana (1)",
        "Cierran el 27/10 (1)",
        "Sin fecha de cierre (1)",
        "Ya cerraron (1)",
    ]
    assert "Cerró 21/10 18:00" in rendered.text


def test_removed_lots_close_the_digest_and_say_so() -> None:
    rendered = render(
        [
            match(lot_id="gone", title="Lote retirado", change="removed"),
            match(lot_id="fresh", title="Lote nuevo", change="new"),
        ]
    )

    assert rendered.text.index("Lote nuevo") < rendered.text.index("Lote retirado")
    assert "Ya no están en la búsqueda (1)" in rendered.text
    assert "ya no está" in rendered.text
    # Nothing is still closing for a lot the search stopped returning.
    assert "Cerraba hoy 20:00" in rendered.text
    assert "Cierra hoy 20:00" not in rendered.text.split("Ya no están")[1]


def test_a_long_run_is_capped_and_announces_the_rest() -> None:
    rendered = render_matches_notification(
        profile_name="Libros antiguos",
        matches=[match(lot_id=f"lot-{index}") for index in range(30)],
        generated_at=NOW,
        timezone=ZONE,
        limit=25,
    )

    assert rendered.html.count("border-radius:14px") == 25
    assert "Y 5 lotes más en Auction Watch." in rendered.text
    assert "Y 5 lotes más en Auction Watch." in rendered.html
    assert rendered.subject == "Auction Watch · 30 lotes para mirar en Libros antiguos"


def test_titles_and_urls_are_escaped() -> None:
    rendered = render([match(title='Lote "raro" & <b>falso</b>', image_url=None)])

    assert "<b>falso</b>" not in rendered.html
    assert "&lt;b&gt;falso&lt;/b&gt;" in rendered.html
    assert "Sin foto" in rendered.html


def test_a_missing_timezone_falls_back_instead_of_failing() -> None:
    rendered = render_matches_notification(
        profile_name="Libros antiguos",
        matches=[match()],
        generated_at=NOW,
        timezone="Mars/Olympus",
    )

    assert "Cierra hoy 23:00" in rendered.text


def profile() -> SearchProfile:
    return SearchProfile(
        id="libros",
        name="Libros antiguos",
        source_ids=["bavastro"],
        keywords_any=["libro"],
        notification_mode="matches",
        schedule={"enabled": True, "times": ["09:00"], "timezone": ZONE},
    )


def test_the_delivered_mail_carries_the_rendered_html(tmp_path: Path) -> None:
    db = Database.open(tmp_path)
    try:
        upgrade_head(tmp_path, db.engine)
        profiles = ProfileRepository(db)
        stored = profiles.create(profile())
        operational = OperationalRepository(db)
        operational.create_run(
            RunRecord(
                run_id="run-1",
                status="completed",
                started_at=NOW,
                finished_at=NOW,
                selected_sources=("bavastro",),
            )
        )
        payload = {"profiles": [{"profile_id": "libros", "matches": [match()]}]}
        operational.record_snapshot(
            "snap-1", "run-1", "hash", "completed", payload, published_at=NOW
        )
        notifications = NotificationRepository(db)
        planner = NotificationPlanner(notifications, enabled=True, timezone=ZONE)

        record = planner.plan(
            stored,
            RunOutcome("run-1", "completed", "snap-1", "hash"),
            SimpleNamespace(snapshot_id="snap-1", payload_json=payload, status="completed"),
            None,
        )
        assert record is not None
        assert "Colección de libros antiguos" in str(record.payload["body"])
        assert "Cierra hoy 20:00" in str(record.payload["body_html"])
        assert record.payload["opportunities"][0]["change"] == "new"  # type: ignore[index]

        sender = FakeNotificationSender()
        assert NotificationDeliveryWorker(notifications, sender, now=lambda: NOW).run_once() is True
        message = sender.messages[0]
        assert message.subject == "Auction Watch · 1 lote para mirar en Libros antiguos"
        assert message.html is not None
        assert "https://example.test/lot-1" in message.html
    finally:
        db.dispose()
