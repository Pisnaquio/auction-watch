"""Render a notification that shows the lots, instead of only counting them.

A mail that says "3 oportunidades nuevas" forces a trip to the app before the
reader knows whether anything matters. The digest below shows each lot the way
the app does: grouped by closing day, soonest first, with the source, the
price, the closing time and why it matched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from auction_watch.sources.registry import DEFAULT_SOURCE_REGISTRY

# An inbox is not a catalog. Beyond this many cards the mail stops being
# readable, so the rest is announced as a count and waits in the app.
MAX_RENDERED_LOTS = 25

_SOURCE_LABELS = {spec.source_id: spec.label for spec in DEFAULT_SOURCE_REGISTRY.specs()}

_INK = "#0b1a2c"
_MUTED = "#3d536c"
_LINK = "#0c8fb0"
_BORDER = "#d8e3f0"
_PAGE = "#f4f7fb"
_SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif"

_CLOSING_UNKNOWN = "Sin fecha de cierre"
_FOOTER = "Abrí Auction Watch para seguir o descartar cada lote."


@dataclass(frozen=True)
class RenderedNotification:
    """The three strings a delivery needs: subject, plain text and HTML."""

    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class LotView:
    """One lot as the reader sees it, already formatted for both bodies."""

    title: str
    url: str
    source: str
    price: str
    closing_at: datetime | None
    closing: str
    image_url: str
    score: int | None
    terms: tuple[str, ...]
    change: str

    @property
    def details(self) -> tuple[str, ...]:
        parts = [self.source, self.price, self.closing]
        if self.score is not None:
            parts.append(f"puntaje {self.score}")
        # A new lot needs no badge: the header already says the mail is about
        # what is new or changed. The other two states would mislead unmarked.
        if self.change == "changed":
            parts.append("con cambios")
        elif self.change == "removed":
            parts.append("ya no está")
        return tuple(part for part in parts if part)

    @property
    def reason(self) -> str:
        if not self.terms:
            return ""
        shown = ", ".join(self.terms[:4])
        return f"coincide: {shown}"


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - an unusable timezone must not stop a mail
        return ZoneInfo("UTC")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _price(lot: Mapping[str, Any]) -> str:
    label = str(lot.get("price_label") or "").strip()
    if label:
        return label
    value = lot.get("price_value")
    currency = str(lot.get("price_currency") or "").strip()
    if value is None or not currency:
        return "Sin precio"
    return f"{currency} {value}"


def _closing_label(
    closing_at: datetime | None,
    now: datetime,
    zone: ZoneInfo,
    *,
    removed: bool = False,
) -> str:
    """Say when it closes the way the app says it, including when it already did."""

    if closing_at is None:
        return _CLOSING_UNKNOWN
    local = closing_at.astimezone(zone)
    time = local.strftime("%H:%M")
    today = now.astimezone(zone).date()
    # A lot the search no longer returns is not closing at any hour; saying it
    # does would invite waiting for an auction that is gone.
    if removed:
        return f"Cerraba hoy {time}" if local.date() == today else f"Cerraba {local:%d/%m} {time}"
    if local.date() == today:
        return f"Cierra hoy {time}"
    day = local.strftime("%d/%m")
    # Sources keep listing lots whose closing date already passed; announcing
    # those as still closing would be a plain lie.
    return f"{'Cerró' if local.date() < today else 'Cierra'} {day} {time}"


def _group_label(closing_at: datetime | None, now: datetime, zone: ZoneInfo) -> str:
    if closing_at is None:
        return _CLOSING_UNKNOWN
    local_day = closing_at.astimezone(zone).date()
    today = now.astimezone(zone).date()
    if local_day < today:
        return "Ya cerraron"
    if local_day == today:
        return "Cierran hoy"
    if local_day == today + timedelta(days=1):
        return "Cierran mañana"
    return f"Cierran el {local_day.strftime('%d/%m')}"


def _lot_view(match: Mapping[str, Any], now: datetime, zone: ZoneInfo) -> LotView:
    lot = match.get("lot")
    lot = lot if isinstance(lot, Mapping) else {}
    source_id = str(lot.get("source_id") or "")
    terms = match.get("matched_terms")
    score = match.get("score")
    closing_at = _parse_datetime(lot.get("closing_at"))
    change = str(match.get("change") or "new")
    return LotView(
        title=str(lot.get("title") or "Lote sin título").strip(),
        url=str(lot.get("lot_url") or lot.get("auction_url") or "").strip(),
        source=_SOURCE_LABELS.get(source_id, source_id.replace("-", " ").title()),
        price=_price(lot),
        closing_at=closing_at,
        closing=_closing_label(closing_at, now, zone, removed=change == "removed"),
        image_url=str(lot.get("image_url") or "").strip(),
        score=score if isinstance(score, int) else None,
        terms=tuple(str(term) for term in terms if str(term).strip())
        if isinstance(terms, Sequence) and not isinstance(terms, str)
        else (),
        change=change,
    )


def _sort_key(view: LotView, now: datetime) -> tuple[int, float, str]:
    """Upcoming first, soonest on top; then undated; then what already closed."""

    if view.change == "removed":
        return (3, 0.0, view.title.casefold())
    if view.closing_at is None:
        return (1, 0.0, view.title.casefold())
    stamp = view.closing_at.timestamp()
    if view.closing_at >= now:
        return (0, stamp, view.title.casefold())
    return (2, -stamp, view.title.casefold())


def _grouped(
    views: Sequence[LotView], now: datetime, zone: ZoneInfo
) -> list[tuple[str, list[LotView]]]:
    groups: list[tuple[str, list[LotView]]] = []
    for view in views:
        label = (
            "Ya no están en la búsqueda"
            if view.change == "removed"
            else _group_label(view.closing_at, now, zone)
        )
        if groups and groups[-1][0] == label:
            groups[-1][1].append(view)
        else:
            groups.append((label, [view]))
    return groups


def _subject(profile_name: str, views: Sequence[LotView]) -> str:
    count = len(views)
    plural = "s" if count != 1 else ""
    if views and all(view.change == "removed" for view in views):
        verb = "están" if count != 1 else "está"
        return f"Auction Watch · {count} lote{plural} que ya no {verb} en {profile_name}"
    return f"Auction Watch · {count} lote{plural} para mirar en {profile_name}"


def _reviewed(generated_at: datetime, zone: ZoneInfo, timezone: str) -> str:
    local = generated_at.astimezone(zone)
    return f"Revisado {local.strftime('%d/%m/%Y %H:%M')} ({timezone})"


def _text_body(
    profile_name: str,
    groups: Sequence[tuple[str, list[LotView]]],
    reviewed: str,
    remaining: int,
) -> str:
    lines = [f"Auction Watch · {profile_name}", reviewed, ""]
    for label, views in groups:
        lines.append(f"{label} ({len(views)})")
        for view in views:
            lines.append(f"· {view.title}")
            lines.append(f"  {' · '.join(view.details)}")
            if view.reason:
                lines.append(f"  {view.reason}")
            if view.url:
                lines.append(f"  {view.url}")
        lines.append("")
    if remaining:
        plural = "s" if remaining != 1 else ""
        lines.append(f"Y {remaining} lote{plural} más en Auction Watch.")
        lines.append("")
    lines.append(_FOOTER)
    return "\n".join(lines)


def _image_cell(view: LotView) -> str:
    cell = (
        "<td width=\"96\" valign=\"top\" style=\"width:96px;padding:14px 0 14px 14px;\">"
    )
    if view.image_url.startswith(("http://", "https://")):
        return (
            f"{cell}<img src=\"{escape(view.image_url, quote=True)}\" "
            f"alt=\"\" width=\"82\" style=\"display:block;width:82px;max-width:82px;"
            f"height:auto;max-height:104px;border:1px solid {_BORDER};"
            "border-radius:10px;\"></td>"
        )
    return (
        f"{cell}<div style=\"width:82px;height:82px;border:1px solid {_BORDER};"
        f"border-radius:10px;font-family:{_SANS};font-size:11px;line-height:82px;"
        f"text-align:center;color:{_MUTED};\">Sin foto</div></td>"
    )


def _card_html(view: LotView) -> str:
    title = escape(view.title, quote=True)
    heading = (
        f"<a href=\"{escape(view.url, quote=True)}\" style=\"color:{_LINK};font-size:16px;"
        f"font-weight:600;text-decoration:none;\">{title}</a>"
        if view.url
        else f"<span style=\"color:{_INK};font-size:16px;font-weight:600;\">{title}</span>"
    )
    reason = (
        f"<div style=\"padding-top:6px;color:{_MUTED};font-size:13px;\">"
        f"{escape(view.reason, quote=True)}</div>"
        if view.reason
        else ""
    )
    return (
        "<tr><td style=\"padding:0 0 12px 0;\">"
        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" "
        f"style=\"border:1px solid {_BORDER};border-radius:14px;background:#ffffff;\">"
        f"<tr>{_image_cell(view)}"
        f"<td valign=\"top\" style=\"padding:14px 16px;font-family:{_SANS};\">"
        f"{heading}"
        f"<div style=\"padding-top:6px;color:{_MUTED};font-size:13px;\">"
        f"{escape(' · '.join(view.details), quote=True)}</div>"
        f"{reason}"
        "</td></tr></table></td></tr>"
    )


def _html_body(
    profile_name: str,
    groups: Sequence[tuple[str, list[LotView]]],
    reviewed: str,
    count: int,
    remaining: int,
) -> str:
    blocks: list[str] = []
    for label, views in groups:
        blocks.append(
            "<tr><td style=\"padding:6px 0 8px 0;font-family:" + _SANS + ";font-size:12px;"
            f"letter-spacing:0.08em;text-transform:uppercase;color:{_MUTED};\">"
            f"{escape(label, quote=True)} · {len(views)}</td></tr>"
        )
        blocks.extend(_card_html(view) for view in views)
    plural = "s" if count != 1 else ""
    tail = ""
    if remaining:
        more = "s" if remaining != 1 else ""
        tail = (
            f"<p style=\"margin:4px 0 0 0;font-family:{_SANS};font-size:13px;color:{_MUTED};\">"
            f"Y {remaining} lote{more} más en Auction Watch.</p>"
        )
    return (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
        "<title>Auction Watch</title></head>"
        f"<body style=\"margin:0;padding:0;background:{_PAGE};\">"
        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" "
        f"style=\"background:{_PAGE};\"><tr><td align=\"center\" style=\"padding:20px 10px;\">"
        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" "
        "style=\"max-width:640px;\">"
        "<tr><td style=\"padding:0 0 14px 0;\">"
        f"<h1 style=\"margin:0;font-family:Georgia,'Times New Roman',serif;font-size:22px;"
        f"color:{_INK};\">Auction Watch · {escape(profile_name, quote=True)}</h1>"
        f"<p style=\"margin:6px 0 0 0;font-family:{_SANS};font-size:13px;color:{_MUTED};\">"
        f"{count} lote{plural} nuevo{plural} o con cambios · {escape(reviewed, quote=True)}</p>"
        "</td></tr>"
        f"{''.join(blocks)}"
        "<tr><td style=\"padding:8px 0 0 0;\">"
        f"<p style=\"margin:0;font-family:{_SANS};font-size:13px;color:{_MUTED};\">"
        f"{_FOOTER}</p>{tail}</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def render_matches_notification(
    *,
    profile_name: str,
    matches: Sequence[Mapping[str, Any]],
    generated_at: datetime,
    timezone: str = "UTC",
    limit: int = MAX_RENDERED_LOTS,
) -> RenderedNotification:
    """Build the subject and both bodies for one batch of new or changed lots."""

    zone = _zone(timezone)
    now = generated_at if generated_at.tzinfo else generated_at.replace(tzinfo=UTC)
    views = sorted(
        (_lot_view(match, now, zone) for match in matches),
        key=lambda view: _sort_key(view, now),
    )
    shown = views[: max(limit, 1)]
    remaining = len(views) - len(shown)
    reviewed = _reviewed(now, zone, timezone)
    groups = _grouped(shown, now, zone)
    return RenderedNotification(
        subject=_subject(profile_name, views),
        text=_text_body(profile_name, groups, reviewed, remaining),
        html=_html_body(profile_name, groups, reviewed, len(views), remaining),
    )


__all__ = [
    "MAX_RENDERED_LOTS",
    "LotView",
    "RenderedNotification",
    "render_matches_notification",
]
