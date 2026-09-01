"""Castells adapter: bounded GXState discovery and verifiable lot pagination."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.core.normalization import contains_term
from auction_watch.core.validation import external_id
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import GroupReceipt, SkippedGroup, SourceScanResult
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
LOT_PAGE_SIZE = 500
MAX_PAGES = 20
MAX_WORKERS = 3
MAX_REQUESTS = 160
MAX_SCAN_SECONDS = 150.0

ART_TITLE_MARKERS = (
    "pinacoteca",
    "pintura",
    "pinturas",
    "arte",
    "obra de arte",
    "obras de arte",
    "escultura",
    "esculturas",
    "acuarela",
    "acuarelas",
    "grabado",
    "grabados",
    "dibujo",
    "dibujos",
    "litografia",
    "litografias",
)
ART_TITLE_MIXED_MARKERS = (
    "antiguedad",
    "antiguedades",
    "mueble",
    "muebles",
    "joya",
    "joyas",
    "reloj",
    "relojes",
    "juguete",
    "juguetes",
    "coleccionable",
    "coleccionables",
    "libro",
    "libros",
    "disco",
    "discos",
    "consola",
    "consolas",
    "videojuego",
    "videojuegos",
    "electronica",
    "vehiculo",
    "vehiculos",
    "maquinaria",
    "herramienta",
    "herramientas",
    "bazar",
    "hogar",
    "varios",
    "general",
)

IssueCategory = Literal[
    "http_error",
    "timeout",
    "invalid_gxstate",
    "invalid_price_currency",
    "pagination",
    "invalid_lot",
    "request_budget",
    "structure_drift",
]

ISSUE_LABELS: dict[IssueCategory, str] = {
    "http_error": "HTTP error",
    "timeout": "timeout",
    "invalid_gxstate": "invalid JSON/GXState",
    "invalid_price_currency": "invalid price/currency",
    "pagination": "incomplete pagination",
    "invalid_lot": "invalid lot",
    "request_budget": "request budget exhausted",
    "structure_drift": "structure drift",
}


class _CastellsIssue(RuntimeError):
    def __init__(self, category: IssueCategory) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class _FetchedLots:
    rows: tuple[Mapping[str, Any], ...]
    invalid_entries: int = 0
    issue: IssueCategory | None = None


@dataclass(frozen=True)
class _GroupScan:
    group: AuctionGroup
    lots: tuple[AuctionLot, ...]
    receipt: GroupReceipt
    issues: tuple[IssueCategory, ...] = ()
    warnings: tuple[IssueCategory, ...] = ()


def _classify_exception(exc: Exception, *, discovery: bool = False) -> IssueCategory:
    if isinstance(exc, _CastellsIssue):
        return exc.category
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_error"
    if isinstance(exc, (httpx.NetworkError, httpx.TransportError)):
        return "http_error"
    message = str(exc).lower()
    if "deadline" in message or "timed out" in message or "timeout" in message:
        return "timeout"
    if "request budget" in message:
        return "request_budget"
    if "pagination" in message or "next cycle" in message or "page limit" in message:
        return "pagination"
    if discovery:
        return "invalid_gxstate"
    return "structure_drift"


def _aggregate_issues(
    groups_by_category: Mapping[IssueCategory, set[str]],
    *,
    discovery_counts: Mapping[IssueCategory, int] | None = None,
) -> tuple[str, ...]:
    summaries: list[str] = []
    for category in ISSUE_LABELS:
        group_count = len(groups_by_category.get(category, set()))
        discovery_count = (discovery_counts or {}).get(category, 0)
        if group_count:
            summaries.append(
                f"Castells {ISSUE_LABELS[category]} ({group_count} "
                f"{'group' if group_count == 1 else 'groups'})"
            )
        if discovery_count:
            summaries.append(
                f"Castells {ISSUE_LABELS[category]} ({discovery_count} discovery "
                f"{'record' if discovery_count == 1 else 'records'})"
            )
    return tuple(summaries)


def _irrelevant_art_title(title: str) -> bool:
    """Classify only clearly art-only auction titles as out of scope."""

    if not title or not any(contains_term(title, marker) for marker in ART_TITLE_MARKERS):
        return False
    return not any(contains_term(title, marker) for marker in ART_TITLE_MIXED_MARKERS)


def parse_gxstate(document: str) -> tuple[Mapping[str, Any], ...]:
    if "GXState" not in document or "RemateImagen" not in document:
        raise _CastellsIssue("invalid_gxstate")
    records: list[Mapping[str, Any]] = []
    for raw in re.findall(r'\{[^{}]*"RemateImagen"[^{}]*"RemateNombre"[^{}]*\}', document):
        try:
            item = json.loads(html.unescape(raw))
        except json.JSONDecodeError:
            continue
        if isinstance(item, Mapping):
            records.append(item)
    if not records:
        raise _CastellsIssue("invalid_gxstate")
    return tuple(records)


def _canonical_auctions(
    records: tuple[Mapping[str, Any], ...],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    Counter[IssueCategory],
    frozenset[str],
]:
    """Collapse repeated GXState records and isolate invalid discovery entries."""

    ordered = sorted(
        records,
        key=lambda raw: (
            clean_text(raw.get("RemateId")),
            json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    unique: dict[str, Mapping[str, Any]] = {}
    discovery_issues: Counter[IssueCategory] = Counter()
    conflicted: set[str] = set()
    for raw in ordered:
        raw_group_id = clean_text(raw.get("RemateId"))
        try:
            group_id = external_id(raw_group_id, "auction_id")
        except ValueError:
            discovery_issues["structure_drift"] += 1
            continue
        previous = unique.get(group_id)
        if previous is None:
            unique[group_id] = raw
            continue
        if previous != raw:
            conflicted.add(group_id)
    return tuple(unique.values()), discovery_issues, frozenset(conflicted)


def _same_source_url(value: Any, fallback: str) -> str:
    candidate = urljoin(WEB_BASE, clean_text(value)) if value else fallback
    parsed = urlsplit(candidate)
    expected = urlsplit(WEB_BASE)
    if parsed.scheme in {"http", "https"} and parsed.netloc == expected.netloc:
        return candidate
    return fallback


def _decode_lot_page(payload: Any) -> tuple[tuple[Any, ...], Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise _CastellsIssue("structure_drift") from exc
    node = payload
    for _depth in range(3):
        if not isinstance(node, Mapping):
            break
        for key in ("data", "Data", "rows", "Rows"):
            rows = node.get(key)
            if isinstance(rows, list):
                next_value = next(
                    (node[name] for name in ("next", "Next", "nextPage") if node.get(name)),
                    None,
                )
                return tuple(rows), next_value
        nested = next(
            (
                node[name]
                for name in ("result", "Result", "payload")
                if isinstance(node.get(name), Mapping)
            ),
            None,
        )
        if nested is None:
            break
        node = nested
    raise _CastellsIssue("structure_drift")


def _pagination_url(current_url: str, next_value: Any) -> str:
    candidate = urljoin(current_url, clean_text(next_value))
    parsed = urlsplit(candidate)
    expected = urlsplit(LOTS_URL)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != expected.netloc:
        raise _CastellsIssue("pagination")
    if parsed.path.rstrip("/") != expected.path.rstrip("/"):
        raise _CastellsIssue("pagination")
    return candidate


def _lot_cursor(item: Any) -> str:
    if not isinstance(item, Mapping):
        return ""
    raw = clean_text(
        item.get("LoteId")
        or item.get("Loteid")
        or item.get("Id")
        or item.get("id")
    )
    return raw.rsplit(":", 1)[-1] if raw else ""


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
        page_size: int = LOT_PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(transport, timeout=timeout)
        if max_workers < 1:
            raise ValueError("Castells max_workers must be positive")
        if max_requests < 1:
            raise ValueError("Castells max_requests must be positive")
        if deadline_seconds <= 0:
            raise ValueError("Castells deadline_seconds must be positive")
        if page_size < 1 or page_size > LOT_PAGE_SIZE:
            raise ValueError("Castells page_size is outside the safe bound")
        if max_pages < 1 or max_pages > MAX_PAGES:
            raise ValueError("Castells max_pages is outside the safe bound")
        self.max_workers = min(max_workers, MAX_WORKERS)
        self.max_requests = max_requests
        self.deadline_seconds = deadline_seconds
        self.page_size = page_size
        self.max_pages = max_pages
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
                raise _CastellsIssue("timeout")
            if self._request_count >= self.max_requests:
                raise _CastellsIssue("request_budget")
            self._request_count += 1
        try:
            return decode_response(
                self.transport.get(
                    url,
                    timeout=min(self.timeout, remaining),
                    deadline=self._deadline,
                )
            )
        except Exception as exc:
            raise _CastellsIssue(_classify_exception(exc)) from exc

    def _fetch_lots(self, group: AuctionGroup, remate_type: int) -> _FetchedLots:
        params: dict[str, Any] = {
            "Remateid": group.auction_id,
            "RemateTipo": remate_type,
            "Cerrado": "false",
            "Lastloteid": 0,
            "Limit": self.page_size,
            "Timezoneoffset": -180,
            "ClienteId": 0,
        }
        rows: list[Mapping[str, Any]] = []
        invalid_entries = 0
        request_url = f"{LOTS_URL}?{urlencode(params)}"
        seen_urls: set[str] = set()
        seen_cursors: set[str] = set()
        for _page in range(self.max_pages):
            if request_url in seen_urls:
                return _FetchedLots(tuple(rows), invalid_entries, "pagination")
            seen_urls.add(request_url)
            try:
                payload = self._get(request_url)
                page_rows, next_value = _decode_lot_page(payload)
            except Exception as exc:
                category = _classify_exception(exc)
                if rows:
                    return _FetchedLots(tuple(rows), invalid_entries, category)
                raise _CastellsIssue(category) from exc
            mappings = tuple(item for item in page_rows if isinstance(item, Mapping))
            invalid_entries += len(page_rows) - len(mappings)
            rows.extend(mappings)
            if next_value:
                try:
                    request_url = _pagination_url(request_url, next_value)
                except _CastellsIssue:
                    return _FetchedLots(tuple(rows), invalid_entries, "pagination")
                continue
            if len(page_rows) < self.page_size:
                return _FetchedLots(tuple(rows), invalid_entries)
            cursor = _lot_cursor(page_rows[-1] if page_rows else None)
            if not cursor or cursor in seen_cursors:
                return _FetchedLots(tuple(rows), invalid_entries, "pagination")
            seen_cursors.add(cursor)
            params["Lastloteid"] = cursor
            request_url = f"{LOTS_URL}?{urlencode(params)}"
        return _FetchedLots(tuple(rows), invalid_entries, "pagination")

    def _scan_group(self, raw: Mapping[str, Any]) -> _GroupScan:
        group_id = external_id(clean_text(raw.get("RemateId")), "auction_id")
        started = datetime.now(UTC)
        canonical_group_url = urljoin(
            WEB_BASE,
            f"frontend.sitio.visualremate.aspx?Remate={group_id}",
        )
        group = AuctionGroup(
            source_id=self.source_id,
            auction_id=group_id,
            title=clean_text(raw.get("RemateNombre")) or f"Remate {group_id}",
            url=_same_source_url(raw.get("Link"), canonical_group_url),
            category=clean_text(raw.get("RemateCategoriaNombre")),
            active=True,
            closing_at=utc_datetime(raw.get("RemateCierre")),
            observed_at=started,
        )
        issues: list[IssueCategory] = []
        warnings: list[IssueCategory] = []
        try:
            try:
                remate_type = int(raw.get("RemateTipo") or 1)
                if remate_type < 1:
                    raise ValueError
            except (TypeError, ValueError):
                remate_type = 1
                issues.append("structure_drift")
            fetched = self._fetch_lots(group, remate_type)
            issues.extend("invalid_lot" for _ in range(fetched.invalid_entries))
            if fetched.issue is not None:
                issues.append(fetched.issue)
            group_lots: dict[str, AuctionLot] = {}
            conflicted_lot_ids: set[str] = set()
            for item in fetched.rows:
                try:
                    lot_id = clean_text(
                        item.get("LoteId")
                        or item.get("Loteid")
                        or item.get("Id")
                        or item.get("id")
                    )
                    title = clean_text(
                        item.get("LoteDescripcion")
                        or item.get("Descripcion")
                        or item.get("LoteTitulo")
                        or item.get("Titulo")
                        or item.get("Nombre")
                    )
                    if not lot_id or not title:
                        raise ValueError("lot lacks stable identity")
                    canonical_lot_url = urljoin(
                        WEB_BASE,
                        "frontend.sitio.visualremate.aspx?"
                        + urlencode({"Remate": group_id, "Lote": lot_id}),
                    )
                    lot_url = _same_source_url(item.get("DetalleUrl"), canonical_lot_url)
                    price_label = clean_text(
                        item.get("ValorActual") or item.get("LotePrecioSalida")
                    )
                    price = decimal_value(price_label)
                    currency = normalize_currency(item.get("LotePrecioSalidaMonedaWF"))
                    if price_label and (price is None or currency is None):
                        warnings.append("invalid_price_currency")
                        price = None
                        currency = None
                    raw_image = first_image(
                        item.get("Imagen") or item.get("image"), base=WEB_BASE
                    )
                    image_url = (_same_source_url(raw_image, "") if raw_image else None) or None
                    candidate = AuctionLot(
                        source_id=self.source_id,
                        auction_id=group_id,
                        lot_id=lot_id,
                        title=title,
                        description=clean_text(
                            item.get("LoteDetalle") or item.get("Detalle")
                        )
                        or title,
                        category=group.category,
                        price_value=price,
                        price_currency=currency if price is not None else None,
                        price_label=price_label,
                        closing_at=utc_datetime(item.get("LoteCierre")) or group.closing_at,
                        lot_url=lot_url,
                        auction_url=group.url,
                        image_url=image_url,
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
                        issues.append("invalid_lot")
                except (TypeError, ValueError):
                    issues.append("invalid_lot")
            ordered_lots = tuple(group_lots[key] for key in sorted(group_lots))
            status = "partial" if issues else "complete"
            receipt = GroupReceipt(
                group_id=group_id,
                status=status,
                inventory_authoritative=status == "complete",
                lot_count=len(ordered_lots),
                error_count=len(issues),
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            return _GroupScan(
                group,
                ordered_lots,
                receipt,
                tuple(issues),
                tuple(warnings),
            )
        except Exception as exc:
            category = _classify_exception(exc)
            receipt = GroupReceipt(
                group_id=group_id,
                status="failed",
                inventory_authoritative=False,
                lot_count=0,
                error_count=1,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            return _GroupScan(group, (), receipt, (category,))

    def scan(self) -> SourceScanResult:
        with self._request_lock:
            self._request_count = 0
            self._deadline = self.clock() + self.deadline_seconds
        try:
            document = self._get(self.discovery_url)
            if not isinstance(document, str):
                raise _CastellsIssue("invalid_gxstate")
            auctions, discovery_issues, conflicted_groups = _canonical_auctions(
                parse_gxstate(document)
            )
        except Exception as exc:
            category = _classify_exception(exc, discovery=True)
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"Castells {ISSUE_LABELS[category]} (discovery)",),
            )

        relevant_auctions: list[Mapping[str, Any]] = []
        skipped_groups: list[SkippedGroup] = []
        for raw in auctions:
            group_id = external_id(clean_text(raw.get("RemateId")), "auction_id")
            title = clean_text(raw.get("RemateNombre"))
            if group_id not in conflicted_groups and _irrelevant_art_title(title):
                skipped_groups.append(SkippedGroup(group_id=group_id, title=title))
            else:
                relevant_auctions.append(raw)

        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        issue_groups: dict[IssueCategory, set[str]] = {
            category: set() for category in ISSUE_LABELS
        }
        warning_groups: dict[IssueCategory, set[str]] = {
            category: set() for category in ISSUE_LABELS
        }
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for scanned in executor.map(self._scan_group, relevant_auctions):
                receipt = scanned.receipt
                issues = list(scanned.issues)
                if receipt.group_id in conflicted_groups:
                    issues.append("structure_drift")
                    receipt = receipt.model_copy(
                        update={
                            "status": "failed" if receipt.status == "failed" else "partial",
                            "inventory_authoritative": False,
                            "error_count": receipt.error_count + 1,
                        }
                    )
                groups.append(scanned.group)
                lots.extend(scanned.lots)
                receipts.append(receipt)
                for category in set(issues):
                    issue_groups[category].add(receipt.group_id)
                for category in set(scanned.warnings):
                    warning_groups[category].add(receipt.group_id)
        errors = _aggregate_issues(issue_groups, discovery_counts=discovery_issues)
        warnings = _aggregate_issues(warning_groups)
        authoritative = not errors and all(
            receipt.status == "complete" and receipt.inventory_authoritative
            for receipt in receipts
        )
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=tuple(groups),
            lots=tuple(lots),
            discovery_status="complete" if authoritative else "partial",
            inventory_authoritative=authoritative,
            receipts=tuple(receipts),
            skipped_groups=tuple(skipped_groups),
            errors=errors,
            warnings=warnings,
        )
