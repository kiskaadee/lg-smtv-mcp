"""Offline contract tests for MCPServer tools, error mapping, and ASGI lifespan."""

import asyncio
import base64
import hashlib
import json
import signal
from collections.abc import AsyncGenerator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.server import create_http_app, create_mcp_server
from lg_smtv_mcp.tv_core import CaptureResult, ConnectionState, TvManager
from tests.conftest import FakeWebSocketTransport

_TEST_JPEG = b"\xff\xd8\xff\xe0test-jpeg"
_TEST_SHA256 = hashlib.sha256(_TEST_JPEG).hexdigest()


class _FakeCapturer:
    def __init__(self, *, error: TvError | None = None) -> None:
        self._error = error
        self.closed = False

    async def capture_frame(
        self,
        ssap_request_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> CaptureResult:
        _ = ssap_request_fn
        if self._error is not None:
            raise self._error
        return CaptureResult(
            image_bytes=_TEST_JPEG,
            frame_sha256=_TEST_SHA256,
            changed_since_last=True,
            size_bytes=len(_TEST_JPEG),
        )

    async def close(self) -> None:
        self.closed = True


def _make_config(
    tmp_path: Path,
    *,
    enable_raw_ssap: bool = False,
    max_volume: int = 30,
) -> TvConfig:
    key_file = tmp_path / ".client_key.json"
    key_file.write_text(json.dumps({"192.0.2.10": "saved-key"}), encoding="utf-8")
    return TvConfig(
        tv_ip="192.0.2.10",
        tv_mac="AA:BB:CC:DD:EE:FF",
        client_key_path=key_file,
        mcp_bearer_token="test-bearer-token",
        enable_raw_ssap=enable_raw_ssap,
        max_volume=max_volume,
    )


def _online_transport(
    sent_requests: list[tuple[str, dict[str, Any]]],
    replies: dict[str, dict[str, Any]] | None = None,
) -> FakeWebSocketTransport:
    replies = replies or {}

    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg: dict[str, Any] = json.loads(raw)
        msg_type = msg.get("type")
        msg_id = msg.get("id")
        if msg_type == "register":
            ws.push_json(
                {"type": "registered", "id": msg_id, "payload": {"client-key": "saved-key"}}
            )
            return
        if msg_type == "subscribe":
            ws.push_json({"type": "response", "id": msg_id, "payload": {"subscribed": True}})
            return
        if msg_type == "request":
            uri = str(msg.get("uri", ""))
            if uri == "ssap://com.webos.service.networkinput/getPointerInputSocket":
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg_id,
                        "payload": {
                            "returnValue": True,
                            "socketPath": "ws://192.0.2.10:3000/pointer",
                        },
                    }
                )
                return
            payload_in = dict(msg.get("payload") or {})
            sent_requests.append((uri, payload_in))
            reply_payload = {"returnValue": True, **replies.get(uri, {})}
            ws.push_json({"type": "response", "id": msg_id, "payload": reply_payload})

    return FakeWebSocketTransport(on_send=on_send)


async def _wait_connected(mgr: TvManager) -> None:
    async def _poll() -> None:
        while mgr.state != ConnectionState.CONNECTED:
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout=2.0)


@asynccontextmanager
async def _started_manager(
    tmp_path: Path,
    *,
    enable_raw_ssap: bool = False,
    max_volume: int = 30,
    capturer: _FakeCapturer | None = None,
    replies: dict[str, dict[str, Any]] | None = None,
) -> AsyncGenerator[tuple[TvConfig, TvManager, list[tuple[str, dict[str, Any]]]]]:
    sent_requests: list[tuple[str, dict[str, Any]]] = []
    pointer_ws = FakeWebSocketTransport()

    async def connect_fn(url: str) -> FakeWebSocketTransport:
        if url.endswith("/pointer"):
            return pointer_ws
        return _online_transport(sent_requests, replies)

    config = _make_config(
        tmp_path,
        enable_raw_ssap=enable_raw_ssap,
        max_volume=max_volume,
    )
    mgr = TvManager(
        config,
        connect_fn=connect_fn,
        capture_client=capturer,
        backoff_steps=(10.0,),
    )
    await mgr.start()
    try:
        await _wait_connected(mgr)
        yield config, mgr, sent_requests
    finally:
        await mgr.close()


async def test_server_registers_12_default_tools_and_gates_raw_ssap(tmp_path: Path) -> None:
    expected_default = {
        "tv_wake",
        "get_state",
        "keys",
        "type_text",
        "screenshot",
        "launch",
        "list_apps",
        "set_volume",
        "set_mute",
        "list_inputs",
        "switch_input",
        "turn_off",
    }

    server_off = create_mcp_server(_make_config(tmp_path, enable_raw_ssap=False))
    names_off = {tool.name for tool in await server_off.list_tools()}
    assert names_off == expected_default
    assert "raw_ssap" not in names_off

    server_on = create_mcp_server(_make_config(tmp_path, enable_raw_ssap=True))
    names_on = {tool.name for tool in await server_on.list_tools()}
    assert names_on == expected_default | {"raw_ssap"}


async def test_server_maps_tv_error_to_tool_error_code_and_message(tmp_path: Path) -> None:
    async def standby_connect(_url: str) -> FakeWebSocketTransport:
        raise TvError(ErrorCode.TV_OFFLINE_STANDBY, "TV is in standby")

    config = _make_config(tmp_path)
    mgr = TvManager(config, connect_fn=standby_connect, backoff_steps=(10.0,))
    await mgr.start()
    try:
        server = create_mcp_server(config, manager=mgr)
        with pytest.raises(ToolError, match=r"TV_OFFLINE_STANDBY: "):
            await server.call_tool("turn_off", {})
    finally:
        await mgr.close()


async def test_server_launch_merges_content_target_and_omits_empty_params(
    tmp_path: Path,
) -> None:
    async with _started_manager(tmp_path) as (config, mgr, sent_requests):
        server = create_mcp_server(config, manager=mgr)
        await server.call_tool("launch", {"app_id": "youtube.leanback.v4"})
        await server.call_tool(
            "launch",
            {
                "app_id": "youtube.leanback.v4",
                "content_target": "v=abc123",
                "params": {"contentTarget": "old", "extra": 1},
            },
        )

    launch_calls = [
        payload for uri, payload in sent_requests if uri == "ssap://system.launcher/launch"
    ]
    assert launch_calls == [
        {"id": "youtube.leanback.v4"},
        {"id": "youtube.leanback.v4", "params": {"contentTarget": "v=abc123", "extra": 1}},
    ]


@pytest.mark.parametrize(
    ("level", "should_succeed"),
    [(-1, False), (0, True), (30, True), (31, False)],
)
async def test_server_set_volume_enforces_max_volume_bounds(
    tmp_path: Path,
    level: int,
    should_succeed: bool,
) -> None:
    async with _started_manager(tmp_path, max_volume=30) as (config, mgr, sent_requests):
        server = create_mcp_server(config, manager=mgr)
        if should_succeed:
            await server.call_tool("set_volume", {"level": level})
            assert ("ssap://audio/setVolume", {"volume": level}) in sent_requests
        else:
            with pytest.raises(ToolError, match=r"INVALID_ARGUMENT: "):
                await server.call_tool("set_volume", {"level": level})
            assert not any(uri == "ssap://audio/setVolume" for uri, _ in sent_requests)


@pytest.mark.parametrize(
    ("text", "should_succeed"),
    [("", False), ("a", True), ("x" * 500, True), ("x" * 501, False)],
)
async def test_server_type_text_validates_length_bounds(
    tmp_path: Path,
    text: str,
    should_succeed: bool,
) -> None:
    async with _started_manager(tmp_path) as (config, mgr, sent_requests):
        server = create_mcp_server(config, manager=mgr)
        if should_succeed:
            await server.call_tool("type_text", {"text": text, "send_enter": False})
            assert (
                "ssap://com.webos.service.ime/insertText",
                {"text": text, "replace": 0},
            ) in sent_requests
        else:
            with pytest.raises(ToolError, match=r"INVALID_ARGUMENT: "):
                await server.call_tool("type_text", {"text": text, "send_enter": False})


async def test_server_screenshot_returns_text_and_image_content(tmp_path: Path) -> None:
    capturer = _FakeCapturer()
    async with _started_manager(tmp_path, capturer=capturer) as (config, mgr, _):
        server = create_mcp_server(config, manager=mgr)
        shot_res = await server.call_tool("screenshot", {})

    assert isinstance(shot_res, CallToolResult)
    assert len(shot_res.content) == 2
    text_block, image_block = shot_res.content[0], shot_res.content[1]
    assert isinstance(text_block, TextContent)
    assert isinstance(image_block, ImageContent)
    shot_meta = json.loads(text_block.text)
    assert shot_meta["frame_sha256"] == _TEST_SHA256
    assert shot_meta["size_bytes"] == len(_TEST_JPEG)
    assert image_block.mime_type == "image/jpeg"
    assert base64.b64decode(image_block.data) == _TEST_JPEG


async def test_server_keys_capture_returns_text_and_image_content(tmp_path: Path) -> None:
    capturer = _FakeCapturer()
    async with _started_manager(tmp_path, capturer=capturer) as (config, mgr, _):
        server = create_mcp_server(config, manager=mgr)
        keys_res = await server.call_tool(
            "keys",
            {"keys": ["UP"], "delay_ms": 0, "settle_ms": 0, "capture": True},
        )

    assert isinstance(keys_res, CallToolResult)
    assert len(keys_res.content) == 2
    text_block, image_block = keys_res.content[0], keys_res.content[1]
    assert isinstance(text_block, TextContent)
    assert isinstance(image_block, ImageContent)
    keys_meta = json.loads(text_block.text)
    assert keys_meta["keys_sent"] is True
    assert keys_meta["count"] == 1
    assert base64.b64decode(image_block.data) == _TEST_JPEG


async def test_server_type_text_capture_returns_text_and_image_content(tmp_path: Path) -> None:
    capturer = _FakeCapturer()
    async with _started_manager(tmp_path, capturer=capturer) as (config, mgr, _):
        server = create_mcp_server(config, manager=mgr)
        text_res = await server.call_tool(
            "type_text",
            {"text": "hi", "send_enter": False, "capture": True},
        )

    assert isinstance(text_res, CallToolResult)
    assert len(text_res.content) == 2
    text_block, image_block = text_res.content[0], text_res.content[1]
    assert isinstance(text_block, TextContent)
    assert isinstance(image_block, ImageContent)
    assert base64.b64decode(image_block.data) == _TEST_JPEG


async def test_server_keys_capture_returns_text_only_on_capture_error(tmp_path: Path) -> None:
    capturer = _FakeCapturer(error=TvError(ErrorCode.SCREENSHOT_UNAVAILABLE, "Dev Mode expired"))
    async with _started_manager(tmp_path, capturer=capturer) as (config, mgr, _):
        server = create_mcp_server(config, manager=mgr)
        res = await server.call_tool(
            "keys",
            {"keys": ["ENTER"], "delay_ms": 0, "settle_ms": 0, "capture": True},
        )

    assert isinstance(res, CallToolResult)
    assert len(res.content) == 1
    text_block = res.content[0]
    assert isinstance(text_block, TextContent)
    meta = json.loads(text_block.text)
    assert meta["keys_sent"] is True
    assert meta["capture_error"] == "SCREENSHOT_UNAVAILABLE: Dev Mode expired"


async def test_server_http_app_rejects_unauthenticated_mcp_request_with_401(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    mgr = TvManager(config, backoff_steps=(10.0,))
    app = create_http_app(config, manager=mgr)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        unauth = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert unauth.status_code == 401


async def test_server_http_app_lifespan_starts_and_closes_manager_with_sighup(
    tmp_path: Path,
) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []
    pointer_ws = FakeWebSocketTransport()
    ssap_ws = _online_transport(sent_requests)

    async def connect_fn(url: str) -> FakeWebSocketTransport:
        if url.endswith("/pointer"):
            return pointer_ws
        return ssap_ws

    config = _make_config(tmp_path)
    mgr = TvManager(config, connect_fn=connect_fn, backoff_steps=(10.0,))
    app = create_http_app(config, manager=mgr)

    incoming: list[MutableMapping[str, Any]] = [
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ]
    outgoing: list[MutableMapping[str, Any]] = []
    startup_done = asyncio.Event()
    allow_shutdown = asyncio.Event()

    async def receive() -> MutableMapping[str, Any]:
        if incoming[0]["type"] == "lifespan.shutdown":
            await allow_shutdown.wait()
        return incoming.pop(0)

    async def send(msg: MutableMapping[str, Any]) -> None:
        outgoing.append(msg)
        if msg["type"] == "lifespan.startup.complete":
            startup_done.set()

    async def _run_lifespan() -> None:
        await app({"type": "lifespan"}, receive, send)

    lifespan_task: asyncio.Task[None] = asyncio.create_task(_run_lifespan())
    try:
        await asyncio.wait_for(startup_done.wait(), timeout=2.0)
        await _wait_connected(mgr)
        loop = asyncio.get_running_loop()
        assert loop.remove_signal_handler(signal.SIGHUP) is True
    finally:
        allow_shutdown.set()
        await asyncio.wait_for(lifespan_task, timeout=2.0)

    assert [m["type"] for m in outgoing] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    assert ssap_ws.closed is True


async def test_server_http_app_allows_authenticated_mcp_initialize_request(
    tmp_path: Path,
) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []
    ssap_ws = _online_transport(sent_requests)

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return ssap_ws

    config = _make_config(tmp_path)
    mgr = TvManager(config, connect_fn=connect_fn, backoff_steps=(10.0,))
    app = create_http_app(config, manager=mgr)

    incoming: list[MutableMapping[str, Any]] = [
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ]
    startup_done = asyncio.Event()
    allow_shutdown = asyncio.Event()

    async def receive() -> MutableMapping[str, Any]:
        if incoming[0]["type"] == "lifespan.shutdown":
            await allow_shutdown.wait()
        return incoming.pop(0)

    async def send(msg: MutableMapping[str, Any]) -> None:
        if msg["type"] == "lifespan.startup.complete":
            startup_done.set()

    async def _run_lifespan() -> None:
        await app({"type": "lifespan"}, receive, send)

    lifespan_task: asyncio.Task[None] = asyncio.create_task(_run_lifespan())
    try:
        await asyncio.wait_for(startup_done.wait(), timeout=2.0)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765"
        ) as client:
            resp = await client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer test-bearer-token",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "0.1.0"},
                    },
                },
            )
        assert resp.status_code == 200
        assert "lg-smtv-mcp" in resp.text
    finally:
        allow_shutdown.set()
        await asyncio.wait_for(lifespan_task, timeout=2.0)
