"""HTTP Bearer authentication ASGI middleware and MCP transport security settings."""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings

from lg_smtv_mcp.config import TvConfig

type Scope = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def build_transport_security_settings(config: TvConfig) -> TransportSecuritySettings:
    """Derive DNS-rebinding TransportSecuritySettings from TvConfig."""
    raise NotImplementedError


class BearerAuthMiddleware:
    """ASGI middleware enforcing constant-time Authorization: Bearer <token>."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        raise NotImplementedError

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError


def wrap_bearer_auth(app: ASGIApp, token: str) -> ASGIApp:
    """Wrap an ASGI application with BearerAuthMiddleware."""
    return BearerAuthMiddleware(app, token)
