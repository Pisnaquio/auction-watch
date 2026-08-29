"""Castells adapter: HTML/GXState discovery plus the public lots endpoint."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urljoin

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import GroupReceipt, SourceScanResult
from auction_watch.sources.parsing import clean_text, decimal_value, first_image, utc_datetime
from auction_watch.sources.transport import decode_response

WEB_BASE = "https://subastascastells.com/"
HOME_URL = urljoin(WEB_BASE, "frontend.home.aspx")
LOTS_URL = urljoin(WEB_BASE, "rest/API/Remate/lotes")
LOT_LIMIT = 9999
MAX_PAGES = 50


def parse_gxstate(document: str) -> tuple[Mapping[str, Any], ...]:
    if "GXState" not in document or "RemateImagen" not in document:
        raise ValueError("Castells response lacks GXState auction marker")
    records: list[Mapping[str, Any]] = []
    for raw in re.findall(r"\{[^{}]*\"RemateImagen\"[^{}]*\"RemateNombre\"[^{}]*\}", document):
        try:
            item = json.loads(html.unescape(raw))
        except json.JSONDecodeError:
            continue
        if isinstance(item, Mapping):
            records.append(item)
    if not records:
        raise ValueError("Castells GXState contains no auction records")
    return tuple(records)


class CastellsSource(BaseAuctionSource):
    source_id = "castells"
    label = "Castells"
    discovery_url = HOME_URL

    def _fetch_lots(
        self, group: AuctionGroup, remate_type: int
    ) -> tuple[tuple[Mapping[str, Any], ...], bool]:
        params = {
            "Remateid": group.auction_id,
            "RemateTipo": remate_type,
            "Cerrado": "false",
            "Lastloteid": 0,
            "Limit": LOT_LIMIT,
            "Timezoneoffset": -180,
            "ClienteId": 0,
        }
        rows: list[Mapping[str, Any]] = []
        request_url = f"{LOTS_URL}?{urlencode(params)}"
        seen_urls: set[str] = set()
        page = 0
        while True:
            page += 1
            if request_url in seen_urls:
                raise ValueError("Castells lots next cycle")
            if page > MAX_PAGES:
                raise ValueError("Castells lots exceeded page limit")
            seen_urls.add(request_url)
            response = self.transport.get(request_url, timeout=self.timeout)
            payload = decode_response(response)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
                raise ValueError("Castells lots response lacks data")
            rows.extend(item for item in payload["data"] if isinstance(item, Mapping))
            next_url = payload.get("next")
            if next_url:
                request_url = urljoin(request_url, str(next_url))
                continue
            if len(payload["data"]) >= LOT_LIMIT:
                return tuple(rows), False
            return tuple(rows), True

    def scan(self) -> SourceScanResult:
        try:
            response = self.transport.get(self.discovery_url, timeout=self.timeout)
            document = decode_response(response)
            if not isinstance(document, str):
                raise ValueError("Castells discovery must be HTML text")
            auctions = parse_gxstate(document)
        except Exception as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"Castells discovery failed ({type(exc).__name__})",),
            )

        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        errors: list[str] = []
        for raw in auctions:
            group_id = clean_text(raw.get("RemateId"))
            started = datetime.now(UTC)
            try:
                if not group_id:
                    raise ValueError("auction lacks RemateId")
                group = AuctionGroup(
                    source_id=self.source_id,
                    auction_id=group_id,
                    title=clean_text(raw.get("RemateNombre")) or f"Remate {group_id}",
                    url=urljoin(
                        WEB_BASE,
                        clean_text(raw.get("Link"))
                        or f"frontend.sitio.visualremate.aspx?Remate={group_id}",
                    ),
                    category=clean_text(raw.get("RemateCategoriaNombre")),
                    active=True,
                    closing_at=utc_datetime(raw.get("RemateCierre")),
                    observed_at=started,
                )
                raw_lots, pagination_complete = self._fetch_lots(
                    group, int(raw.get("RemateTipo") or 1)
                )
                if not pagination_complete:
                    raise ValueError("lots pagination reached the hard limit")
                group_lots: list[AuctionLot] = []
                for item in raw_lots:
                    lot_id = clean_text(item.get("LoteId") or item.get("Id") or item.get("id"))
                    title = clean_text(item.get("LoteDescripcion") or item.get("Descripcion"))
                    raw_lot_url = clean_text(item.get("DetalleUrl"))
                    lot_url = urljoin(WEB_BASE, raw_lot_url)
                    if not lot_id or not title or not raw_lot_url:
                        errors.append(f"Castells group {group_id}: malformed lot")
                        continue
                    price = decimal_value(item.get("ValorActual") or item.get("LotePrecioSalida"))
                    currency = clean_text(item.get("LotePrecioSalidaMonedaWF") or "UYU").upper()
                    group_lots.append(
                        AuctionLot(
                            source_id=self.source_id,
                            auction_id=group_id,
                            lot_id=lot_id,
                            title=title,
                            description=title,
                            category=group.category,
                            price_value=price,
                            price_currency=currency if price is not None else None,
                            price_label=clean_text(
                                item.get("ValorActual") or item.get("LotePrecioSalida")
                            ),
                            closing_at=utc_datetime(item.get("LoteCierre")) or group.closing_at,
                            lot_url=lot_url,
                            auction_url=group.url,
                            image_url=first_image(
                                item.get("Imagen") or item.get("image"), base=WEB_BASE
                            ),
                            active=clean_text(item.get("Estado") or "active").lower()
                            not in {"cerrado", "closed"},
                            observed_at=started,
                        )
                    )
                groups.append(group)
                lots.extend(group_lots)
                partial = any(f"group {group_id}" in error for error in errors)
                receipts.append(
                    GroupReceipt(
                        group_id=group_id,
                        status="partial" if partial else "complete",
                        inventory_authoritative=not partial,
                        lot_count=len(group_lots),
                        error_count=1 if partial else 0,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                )
            except Exception as exc:
                errors.append(f"Castells group {group_id or 'unknown'}: {type(exc).__name__}")
                receipts.append(
                    GroupReceipt(
                        group_id=group_id or "unknown",
                        status="failed",
                        lot_count=0,
                        error_count=1,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                )
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=tuple(groups),
            lots=tuple(lots),
            discovery_status="complete" if not errors else "partial",
            inventory_authoritative=not errors,
            receipts=tuple(receipts),
            errors=tuple(errors),
        )
