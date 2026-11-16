"""HTTP Bearer authentication ASGI middleware and MCP transport security settings."""

import hmac
import json
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.errors import ConfigError

type Scope = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_UNAUTHORIZED_BODY = json.dumps({"error": "unauthorized"}).encode("utf-8")


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _add_host_variants(hosts: list[str], origins: list[str], raw_host: str, port: int) -> None:
    entry = raw_host.strip()
    if not entry:
        return
    if entry.startswith(("http://", "https://")):
        origins.append(entry)
        return
    hosts.append(entry)
    if ":" not in entry:
        hosts.append(f"{entry}:{port}")
        for scheme in ("http", "https"):
            origins.append(f"{scheme}://{entry}")
            origins.append(f"{scheme}://{entry}:{port}")
    else:
        for scheme in ("http", "https"):
            origins.append(f"{scheme}://{entry}")


def build_transport_security_settings(config: TvConfig) -> TransportSecuritySettings:
    """Derive DNS-rebinding TransportSecuritySettings from TvConfig."""
    hosts: list[str] = []
    origins: list[str] = []

    for local_host in ("127.0.0.1", "localhost"):
        hosts.extend([f"{local_host}:{config.port}", f"{local_host}:*", local_host])
        for scheme in ("http", "https"):
            origins.extend([f"{scheme}://{local_host}:{config.port}", f"{scheme}://{local_host}:*"])

    if config.host and config.host not in {"0.0.0.0", "::"}:
        _add_host_variants(hosts, origins, config.host, config.port)

    for extra_host in config.allowed_hosts:
        _add_host_variants(hosts, origins, extra_host, config.port)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_dedupe(hosts),
        allowed_origins=_dedupe(origins),
    )


class BearerAuthMiddleware:
    """ASGI middleware enforcing constant-time Authorization: Bearer <token>."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        cleaned = token.strip()
        if not cleaned:
            raise ConfigError("MCP_BEARER_TOKEN must be a non-empty string")
        self._app = app
        self._token_bytes = cleaned.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        if not self._is_authorized(scope):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": _UNAUTHORIZED_BODY,
                    "more_body": False,
                }
            )
            return

        await self._app(scope, receive, send)

    def _is_authorized(self, scope: Scope) -> bool:
        headers: Iterable[tuple[bytes, bytes]] = scope.get("headers", ())
        auth_value: str | None = None
        for name, value in headers:
            if name.lower() == b"authorization":
                auth_value = value.decode("latin-1")
                break
        if auth_value is None:
            return False

        parts = auth_value.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            return False

        return hmac.compare_digest(parts[1].encode("utf-8"), self._token_bytes)


def wrap_bearer_auth(app: ASGIApp, token: str) -> ASGIApp:
    """Wrap an ASGI application with BearerAuthMiddleware."""
    return BearerAuthMiddleware(app, token)
