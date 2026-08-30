"""Castells adapter: HTML/GXState discovery plus the public lots endpoint."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urljoin

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import GroupReceipt, SourceScanResult
from auction_watch.sources.parsing import (
    clean_text,
    decimal_value,
    first_image,
    normalize_currency,
    utc_datetime,
)
from auction_watch.sources.transport import Transport, decode_response

WEB_BASE = "https://subastascastells.com/"
HOME_URL = urljoin(WEB_BASE, "frontend.home.aspx")
LOTS_URL = urljoin(WEB_BASE, "rest/API/Remate/lotes")
LOT_LIMIT = 9999
MAX_PAGES = 50
MAX_WORKERS = 6
MAX_REQUESTS = 160
# Six bounded workers keep the normal scan below the observed two-minute run.
MAX_SCAN_SECONDS = 90.0


def _error_label(exc: Exception) -> str:
    message = str(exc)
    if message == "Castells scan deadline exceeded":
        return "deadline exceeded"
    if message == "Castells scan request budget exhausted":
        return "request budget exhausted"
    return type(exc).__name__


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


def _canonical_auctions(
    records: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...], frozenset[str]]:
    """Collapse repeated GXState render records before scheduling network work."""

    ordered = sorted(
        records,
        key=lambda raw: (
            clean_text(raw.get("RemateId")),
            json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    unique: dict[str, Mapping[str, Any]] = {}
    errors: list[str] = []
    conflicted: set[str] = set()
    for raw in ordered:
        group_id = clean_text(raw.get("RemateId"))
        if not group_id:
            errors.append("Castells discovery item lacks RemateId")
            continue
        previous = unique.get(group_id)
        if previous is None:
            unique[group_id] = raw
            continue
        if previous != raw:
            errors.append(f"Castells group {group_id}: conflicting discovery records")
            conflicted.add(group_id)
    return tuple(unique.values()), tuple(errors), frozenset(conflicted)


class CastellsSource(BaseAuctionSource):
    source_id = "castells"
    label = "Castells"
    discovery_url = HOME_URL

    def __init__(
        self,
        transport: Transport,
        *,
        timeout: float | None = None,
        max_workers: int = MAX_WORKERS,
        max_requests: int = MAX_REQUESTS,
        deadline_seconds: float = MAX_SCAN_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(transport, timeout=timeout)
        if max_workers < 1:
            raise ValueError("Castells max_workers must be positive")
        if max_requests < 1:
            raise ValueError("Castells max_requests must be positive")
        if deadline_seconds <= 0:
            raise ValueError("Castells deadline_seconds must be positive")
        self.max_workers = min(max_workers, MAX_WORKERS)
        self.max_requests = max_requests
        self.deadline_seconds = deadline_seconds
        self.clock = clock
        self._request_count = 0
        self._request_lock = Lock()
        self._deadline: float | None = None

    def _get(self, url: str) -> Any:
        with self._request_lock:
            if self._deadline is None:
                raise RuntimeError("Castells scan deadline is not initialized")
            remaining = self._deadline - self.clock()
            if remaining <= 0:
                raise RuntimeError("Castells scan deadline exceeded")
            if self._request_count >= self.max_requests:
                raise RuntimeError("Castells scan request budget exhausted")
            self._request_count += 1
        return decode_response(
            self.transport.get(
                url,
                timeout=min(self.timeout, remaining),
                deadline=self._deadline,
            )
        )

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
            payload = self._get(request_url)
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

    def _scan_group(
        self, raw: Mapping[str, Any]
    ) -> tuple[AuctionGroup | None, tuple[AuctionLot, ...], GroupReceipt, tuple[str, ...]]:
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
            group_lots: dict[str, AuctionLot] = {}
            conflicted_lot_ids: set[str] = set()
            group_errors: list[str] = []
            for item in raw_lots:
                try:
                    lot_id = clean_text(item.get("LoteId") or item.get("Id") or item.get("id"))
                    title = clean_text(item.get("LoteDescripcion") or item.get("Descripcion"))
                    raw_lot_url = clean_text(item.get("DetalleUrl"))
                    if not lot_id or not title or not raw_lot_url:
                        raise ValueError("lot lacks stable fields")
                    price_label = clean_text(
                        item.get("ValorActual") or item.get("LotePrecioSalida")
                    )
                    price = decimal_value(price_label)
                    currency = normalize_currency(item.get("LotePrecioSalidaMonedaWF"))
                    if price is not None and currency is None:
                        group_errors.append(f"Castells group {group_id}: unknown currency")
                        price = None
                    candidate = AuctionLot(
                        source_id=self.source_id,
                        auction_id=group_id,
                        lot_id=lot_id,
                        title=title,
                        description=title,
                        category=group.category,
                        price_value=price,
                        price_currency=currency if price is not None else None,
                        price_label=price_label,
                        closing_at=utc_datetime(item.get("LoteCierre")) or group.closing_at,
                        lot_url=urljoin(WEB_BASE, raw_lot_url),
                        auction_url=group.url,
                        image_url=first_image(
                            item.get("Imagen") or item.get("image"), base=WEB_BASE
                        ),
                        active=clean_text(item.get("Estado") or "active").lower()
                        not in {"cerrado", "closed"},
                        observed_at=started,
                    )
                    if lot_id in conflicted_lot_ids:
                        continue
                    previous = group_lots.get(lot_id)
                    if previous is None:
                        group_lots[lot_id] = candidate
                    elif previous != candidate:
                        group_lots.pop(lot_id)
                        conflicted_lot_ids.add(lot_id)
                        group_errors.append(
                            f"Castells group {group_id}: conflicting duplicate lot"
                        )
                except (TypeError, ValueError):
                    group_errors.append(f"Castells group {group_id}: malformed lot")
            ordered_lots = tuple(group_lots[key] for key in sorted(group_lots))
            status = "partial" if group_errors else "complete"
            receipt = GroupReceipt(
                group_id=group_id,
                status=status,
                inventory_authoritative=status == "complete",
                lot_count=len(ordered_lots),
                error_count=len(group_errors),
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            return group, ordered_lots, receipt, tuple(group_errors)
        except Exception as exc:
            error = f"Castells group {group_id or 'unknown'}: {_error_label(exc)}"
            receipt = GroupReceipt(
                group_id=group_id or "unknown",
                status="failed",
                inventory_authoritative=False,
                lot_count=0,
                error_count=1,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            return None, (), receipt, (error,)

    def scan(self) -> SourceScanResult:
        with self._request_lock:
            self._request_count = 0
            self._deadline = self.clock() + self.deadline_seconds
        try:
            document = self._get(self.discovery_url)
            if not isinstance(document, str):
                raise ValueError("Castells discovery must be HTML text")
            auctions, discovery_errors, conflicted_groups = _canonical_auctions(
                parse_gxstate(document)
            )
        except Exception as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"Castells discovery failed ({_error_label(exc)})",),
            )

        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        errors: list[str] = list(discovery_errors)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            results = executor.map(self._scan_group, auctions)
            for group, group_lots, receipt, group_errors in results:
                if receipt.group_id in conflicted_groups:
                    receipt = receipt.model_copy(
                        update={
                            "status": "failed" if receipt.status == "failed" else "partial",
                            "inventory_authoritative": False,
                            "error_count": receipt.error_count + 1,
                        }
                    )
                if group is not None:
                    groups.append(group)
                    lots.extend(group_lots)
                receipts.append(receipt)
                errors.extend(group_errors)
        if self._deadline is not None and self.clock() >= self._deadline:
            if "Castells scan deadline exceeded" not in errors:
                errors.append("Castells scan deadline exceeded")
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
