"""Offline contract tests for lg-smtv-mcp CLI (serve and pair)."""

import json
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from websockets.exceptions import InvalidMessage

from lg_smtv_mcp.auth import ASGIApp
from lg_smtv_mcp.cli import main
from lg_smtv_mcp.config import TvConfig
from tests.conftest import FakeWebSocketTransport


def _make_config(
    tmp_path: Path,
    *,
    token: str | None = "test-token",
    ssh_key_path: Path | None = None,
) -> TvConfig:
    return TvConfig(
        tv_ip="192.0.2.10",
        tv_mac="AA:BB:CC:DD:EE:FF",
        client_key_path=tmp_path / ".client_key.json",
        ssh_key_path=ssh_key_path,
        mcp_bearer_token=token,
    )


@pytest.mark.parametrize("bad_token", [None, "", "   "])
def test_cli_serve_http_fails_closed_when_bearer_token_missing_or_blank(
    tmp_path: Path,
    bad_token: str | None,
) -> None:
    called = False

    def fake_uvicorn(app: ASGIApp, host: str, port: int) -> None:
        nonlocal called
        _ = (app, host, port)
        called = True

    rc = main(
        ["serve", "--transport", "streamable-http"],
        config_loader=lambda: _make_config(tmp_path, token=bad_token),
        uvicorn_run=fake_uvicorn,
    )

    assert rc == 1
    assert called is False


async def test_cli_serve_http_runs_uvicorn_with_configured_host_and_port(tmp_path: Path) -> None:
    captured: list[tuple[ASGIApp, str, int]] = []

    def fake_uvicorn(app: ASGIApp, host: str, port: int) -> None:
        captured.append((app, host, port))

    rc = main(
        ["serve", "--transport", "streamable-http"],
        config_loader=lambda: _make_config(tmp_path, token="valid-secret"),
        uvicorn_run=fake_uvicorn,
    )

    assert rc == 0
    assert len(captured) == 1
    app, host, port = captured[0]
    assert host == "127.0.0.1"
    assert port == 8765

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        unauth = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert unauth.status_code == 401


def test_cli_serve_stdio_delegates_to_stdio_runner_without_bearer_token(tmp_path: Path) -> None:
    ran: list[MCPServer[Any]] = []

    def fake_stdio(server: MCPServer[Any]) -> None:
        ran.append(server)

    rc = main(
        ["serve", "--transport", "stdio"],
        config_loader=lambda: _make_config(tmp_path, token=None),
        stdio_run=fake_stdio,
    )

    assert rc == 0
    assert len(ran) == 1
    assert isinstance(ran[0], MCPServer)


def test_cli_pair_merges_existing_client_keys_and_writes_0600(tmp_path: Path) -> None:
    key_file = tmp_path / ".client_key.json"
    key_file.write_text(json.dumps({"192.0.2.10": "old-key"}), encoding="utf-8")

    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg: dict[str, Any] = json.loads(raw)
        if msg.get("type") == "register":
            ws.push_json(
                {
                    "type": "registered",
                    "id": msg["id"],
                    "payload": {"client-key": "new-key-20"},
                }
            )

    ws = FakeWebSocketTransport(on_send=on_send)
    connected_urls: list[str] = []

    async def fake_connect(url: str) -> FakeWebSocketTransport:
        connected_urls.append(url)
        return ws

    rc = main(
        ["pair", "--ip", "192.0.2.20"],
        config_loader=lambda: _make_config(tmp_path),
        connect_fn=fake_connect,
    )

    assert rc == 0
    assert connected_urls == ["wss://192.0.2.20:3001"]
    saved = json.loads(key_file.read_text(encoding="utf-8"))
    assert saved == {"192.0.2.10": "old-key", "192.0.2.20": "new-key-20"}
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_cli_pair_fetch_ssh_key_saves_key_with_0600_permissions(tmp_path: Path) -> None:
    ssh_dest = tmp_path / "webos_rsa"
    fetched_urls: list[str] = []

    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg: dict[str, Any] = json.loads(raw)
        if msg.get("type") == "register":
            ws.push_json(
                {
                    "type": "registered",
                    "id": msg["id"],
                    "payload": {"client-key": "tv-key-10"},
                }
            )

    async def fake_connect(_url: str) -> FakeWebSocketTransport:
        return FakeWebSocketTransport(on_send=on_send)

    def fake_fetch(url: str) -> bytes:
        fetched_urls.append(url)
        return b"-----BEGIN RSA PRIVATE KEY-----\nTEST\n-----END RSA PRIVATE KEY-----\n"

    rc = main(
        ["pair", "--fetch-ssh-key", "--ssh-key-path", str(ssh_dest)],
        config_loader=lambda: _make_config(tmp_path),
        connect_fn=fake_connect,
        fetch_url=fake_fetch,
    )

    assert rc == 0
    assert fetched_urls == ["http://192.0.2.10:9991/webos_rsa"]
    assert ssh_dest.read_bytes().startswith(b"-----BEGIN RSA PRIVATE KEY-----")
    assert stat.S_IMODE(ssh_dest.stat().st_mode) == 0o600


def test_cli_pair_fetch_ssh_key_failure_preserves_client_key_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / ".client_key.json"
    ssh_dest = tmp_path / "webos_rsa"

    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg: dict[str, Any] = json.loads(raw)
        if msg.get("type") == "register":
            ws.push_json(
                {
                    "type": "registered",
                    "id": msg["id"],
                    "payload": {"client-key": "tv-key-10"},
                }
            )

    async def fake_connect(_url: str) -> FakeWebSocketTransport:
        return FakeWebSocketTransport(on_send=on_send)

    def failing_fetch(_url: str) -> bytes:
        raise OSError("connection refused")

    rc = main(
        ["pair", "--fetch-ssh-key", "--ssh-key-path", str(ssh_dest)],
        config_loader=lambda: _make_config(tmp_path),
        connect_fn=fake_connect,
        fetch_url=failing_fetch,
    )

    assert rc == 1
    assert json.loads(key_file.read_text(encoding="utf-8")) == {"192.0.2.10": "tv-key-10"}
    assert not ssh_dest.exists()


def test_cli_pair_returns_nonzero_on_websocket_handshake_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def failing_connect(_url: str) -> FakeWebSocketTransport:
        raise InvalidMessage("did not receive a valid HTTP response")

    rc = main(
        ["pair"],
        config_loader=lambda: _make_config(tmp_path),
        connect_fn=failing_connect,
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "error: did not receive a valid HTTP response" in captured.err
