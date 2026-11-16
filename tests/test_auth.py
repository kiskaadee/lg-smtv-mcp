"""Offline contract tests for BearerAuthMiddleware and TransportSecuritySettings."""

from collections.abc import MutableMapping
from typing import Any

import httpx
import pytest

from lg_smtv_mcp.auth import (
    Receive,
    Scope,
    Send,
    build_transport_security_settings,
    wrap_bearer_auth,
)
from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.errors import ConfigError


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    _ = (scope, receive)
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"ok":true}', "more_body": False})


async def test_bearer_auth_allows_valid_token_and_forwards_to_app() -> None:
    app = wrap_bearer_auth(_ok_app, "secret-token-123")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/mcp", headers={"Authorization": "Bearer secret-token-123"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Bearer secret-token-1234"},
    ],
)
async def test_bearer_auth_rejects_missing_malformed_or_invalid_token_with_401(
    headers: dict[str, str],
) -> None:
    app = wrap_bearer_auth(_ok_app, "secret-token-123")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/mcp", headers=headers)

    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"
    assert resp.json() == {"error": "unauthorized"}


async def test_bearer_auth_passes_through_non_http_lifespan_scope() -> None:
    seen_scopes: list[str] = []

    async def lifespan_app(scope: Scope, receive: Receive, send: Send) -> None:
        _ = (receive, send)
        seen_scopes.append(str(scope["type"]))

    app = wrap_bearer_auth(lifespan_app, "secret-token-123")

    async def dummy_receive() -> MutableMapping[str, Any]:
        return {"type": "lifespan.startup"}

    async def dummy_send(message: MutableMapping[str, Any]) -> None:
        _ = message

    await app({"type": "lifespan"}, dummy_receive, dummy_send)
    assert seen_scopes == ["lifespan"]


@pytest.mark.parametrize("bad_token", ["", "   "])
def test_bearer_auth_rejects_blank_server_token_with_config_error(bad_token: str) -> None:
    with pytest.raises(ConfigError):
        wrap_bearer_auth(_ok_app, bad_token)


def test_transport_security_settings_includes_local_bound_host_and_allowed_hosts() -> None:
    config = TvConfig(
        tv_ip="192.0.2.10",
        host="192.0.2.50",
        port=9000,
        allowed_hosts=("tv-mcp.local", "198.51.100.5:9000"),
    )
    settings = build_transport_security_settings(config)

    assert settings.enable_dns_rebinding_protection is True
    assert "127.0.0.1:9000" in settings.allowed_hosts
    assert "localhost:9000" in settings.allowed_hosts
    assert "192.0.2.50:9000" in settings.allowed_hosts
    assert "tv-mcp.local" in settings.allowed_hosts
    assert "tv-mcp.local:9000" in settings.allowed_hosts
    assert "198.51.100.5:9000" in settings.allowed_hosts
    assert "http://192.0.2.50:9000" in settings.allowed_origins
    assert "http://tv-mcp.local:9000" in settings.allowed_origins


def test_transport_security_settings_excludes_wildcard_bind_host_from_allowed_hosts() -> None:
    config = TvConfig(tv_ip="192.0.2.10", host="0.0.0.0", port=8765)
    settings = build_transport_security_settings(config)

    assert "0.0.0.0" not in settings.allowed_hosts
    assert "0.0.0.0:8765" not in settings.allowed_hosts
    assert "127.0.0.1:8765" in settings.allowed_hosts
    assert "localhost:8765" in settings.allowed_hosts
