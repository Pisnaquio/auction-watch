"""Small same-origin guard for the ingress-facing HTTP boundary."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return cast(str, value.decode("latin-1"))
    return None


def _host_is_sane(host: str | None) -> bool:
    if not host or any(char.isspace() or ord(char) < 32 for char in host):
        return False
    parsed = urlsplit(f"//{host}")
    return bool(parsed.hostname)


def _same_origin(origin: str, hosts: Iterable[str]) -> bool:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    origin_host = parsed.hostname.lower()
    try:
        origin_port = parsed.port
    except ValueError:
        return False
    for raw_host in hosts:
        host = raw_host.strip().lower()
        if not host:
            continue
        parsed_host = urlsplit(f"//{host}")
        if not parsed_host.hostname or parsed_host.hostname.lower() != origin_host:
            continue
        try:
            host_port = parsed_host.port
        except ValueError:
            continue
        if (origin_port or (443 if parsed.scheme == "https" else 80)) == (
            host_port or (443 if parsed.scheme == "https" else 80)
        ):
            return True
    return False


class IngressSecurityMiddleware:
    """Reject malformed hosts and cross-origin API requests without CORS."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        ingress_path = (_header(scope, b"x-ingress-path") or "").rstrip("/")
        request_path = str(scope.get("path", ""))
        if ingress_path and request_path.startswith(f"{ingress_path}/"):
            scope = dict(scope)
            stripped_path = request_path[len(ingress_path) :] or "/"
            scope["path"] = stripped_path
            scope["raw_path"] = stripped_path.encode("utf-8")
            scope["root_path"] = ingress_path
        host = _header(scope, b"host")
        if not _host_is_sane(host):
            await self._reject(send)
            return
        origin = _header(scope, b"origin")
        if origin:
            forwarded_host = _header(scope, b"x-forwarded-host")
            candidates = tuple(
                candidate for candidate in (host, forwarded_host) if candidate is not None
            )
            if not _same_origin(origin, candidates):
                await self._reject(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"request origin rejected"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
