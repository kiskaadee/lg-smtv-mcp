"""End-to-end MVP lifecycle test over in-process Streamable HTTP MCP ClientSession."""

import asyncio
import json
from collections.abc import MutableMapping
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.server import create_http_app
from lg_smtv_mcp.tv_core import TvManager
from tests.conftest import FakeWebSocketTransport


def _extract_json_text(content: list[Any]) -> dict[str, Any]:
    assert content, "Expected non-empty tool content"
    first = content[0]
    assert isinstance(first, TextContent)
    parsed: Any = json.loads(first.text)
    assert isinstance(parsed, dict)
    return cast(dict[str, Any], parsed)


def _extract_error_text(content: list[Any]) -> str:
    return " ".join(item.text for item in content if isinstance(item, TextContent))


def _build_online_ssap(
    tv_ip: str,
    sent_requests: list[tuple[str, dict[str, Any]]],
) -> FakeWebSocketTransport:
    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg: dict[str, Any] = json.loads(raw)
        msg_type = msg.get("type")
        msg_id = msg.get("id")
        if msg_type == "register":
            ws.push_json(
                {
                    "type": "registered",
                    "id": msg_id,
                    "payload": {"client-key": "e2e-saved-key"},
                }
            )
            return
        if msg_type == "subscribe":
            uri = str(msg.get("uri", ""))
            if uri == "ssap://audio/getStatus":
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg_id,
                        "payload": {
                            "subscribed": True,
                            "volume": 10,
                            "mute": False,
                            "volumeStatus": {"soundOutput": "tv_speaker"},
                        },
                    }
                )
                return
            if uri == "ssap://com.webos.applicationManager/getForegroundAppInfo":
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg_id,
                        "payload": {
                            "subscribed": True,
                            "appId": "com.webos.app.home",
                        },
                    }
                )
                return
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
                            "socketPath": f"ws://{tv_ip}:3000/pointer",
                        },
                    }
                )
                return
            payload_in = dict(msg.get("payload") or {})
            sent_requests.append((uri, payload_in))
            ws.push_json({"type": "response", "id": msg_id, "payload": {"returnValue": True}})

    return FakeWebSocketTransport(on_send=on_send)


async def test_e2e_mvp_lifecycle_from_standby_through_clean_shutdown(tmp_path: Path) -> None:
    tasks_before = set(asyncio.all_tasks())

    tv_ip = "192.0.2.42"
    tv_mac = "AA:BB:CC:DD:EE:FF"
    bearer_token = "e2e-test-bearer-token"
    key_file = tmp_path / ".client_key.json"
    key_file.write_text(json.dumps({tv_ip: "e2e-saved-key"}), encoding="utf-8")

    config = TvConfig(
        tv_ip=tv_ip,
        tv_mac=tv_mac,
        client_key_path=key_file,
        mcp_bearer_token=bearer_token,
        max_volume=40,
    )

    tv_awake = False
    wol_calls: list[tuple[str, str, int]] = []
    sent_requests: list[tuple[str, dict[str, Any]]] = []
    standby_backoff_entered = asyncio.Event()
    turn_off_completed = asyncio.Event()
    post_turn_off_standby_entered = asyncio.Event()
    pointer_ws = FakeWebSocketTransport()
    online_ssap_connections: list[FakeWebSocketTransport] = []

    def fake_send_wol(mac: str, broadcast_addr: str, port: int) -> None:
        nonlocal tv_awake
        wol_calls.append((mac, broadcast_addr, port))
        tv_awake = True

    async def fake_wait_fn(event: asyncio.Event, timeout: float) -> None:
        if turn_off_completed.is_set():
            post_turn_off_standby_entered.set()
        else:
            standby_backoff_entered.set()
        with suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=timeout)

    async def connect_fn(url: str) -> FakeWebSocketTransport:
        if url.endswith("/pointer"):
            return pointer_ws
        if not tv_awake:
            standby_ws = FakeWebSocketTransport()
            standby_ws.push_close(1008, "Try Again Later (EWS)")
            return standby_ws
        online_ws = _build_online_ssap(tv_ip, sent_requests)
        online_ssap_connections.append(online_ws)
        return online_ws

    manager = TvManager(
        config,
        connect_fn=connect_fn,
        wol_fn=fake_send_wol,
        backoff_steps=(10.0,),
        wake_poll_interval=0.01,
        wait_fn=fake_wait_fn,
    )
    app = create_http_app(config, manager=manager)

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

    startup_error: list[str] = []

    async def send(msg: MutableMapping[str, Any]) -> None:
        if msg["type"] == "lifespan.startup.complete":
            startup_done.set()
        elif msg["type"] == "lifespan.startup.failed":
            startup_error.append(str(msg.get("message", "ASGI startup failed")))
            startup_done.set()

    async def _run_lifespan() -> None:
        await app({"type": "lifespan"}, receive, send)

    lifespan_task: asyncio.Task[None] = asyncio.create_task(_run_lifespan())
    try:
        startup_wait_task = asyncio.create_task(startup_done.wait())
        try:
            done, _ = await asyncio.wait(
                {startup_wait_task, lifespan_task},
                timeout=2.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert done, "ASGI lifespan startup did not finish within 2.0s"
            if lifespan_task in done:
                await lifespan_task
                raise AssertionError("ASGI lifespan exited before startup completed")
            assert not startup_error, f"ASGI startup failed: {startup_error[0]}"
        finally:
            if not startup_wait_task.done():
                startup_wait_task.cancel()
                with suppress(asyncio.CancelledError):
                    await startup_wait_task
        await asyncio.wait_for(standby_backoff_entered.wait(), timeout=2.0)

        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:8765",
                headers={"Authorization": f"Bearer {bearer_token}"},
            ) as http_client,
            streamable_http_client(
                "http://127.0.0.1:8765/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()

            # 1. Standby (1008): launch fails fast with TV_OFFLINE_STANDBY and no implicit WoL.
            standby_launch = await session.call_tool(
                "launch",
                {"app_id": "youtube.leanback.v4"},
            )
            assert standby_launch.is_error is True
            assert "TV_OFFLINE_STANDBY" in _extract_error_text(standby_launch.content)
            assert wol_calls == []

            # 2. tv_wake: sends WoL magic packet, connects, and subscribes.
            wake_res = await session.call_tool("tv_wake", {})
            assert wake_res.is_error is False
            wake_payload = _extract_json_text(wake_res.content)
            assert wake_payload["status"] == "connected"
            assert isinstance(wake_payload["elapsed_ms"], int) and wake_payload["elapsed_ms"] >= 0
            assert wol_calls == [(tv_mac, "255.255.255.255", 9)]

            # 3. get_state: returns connected, fresh subscribed state.
            state_res = await session.call_tool("get_state", {})
            assert state_res.is_error is False
            state_payload = _extract_json_text(state_res.content)
            assert state_payload["connected"] is True
            assert state_payload["stale"] is False
            assert state_payload["volume"] == 10
            assert state_payload["muted"] is False
            assert state_payload["sound_output"] == "tv_speaker"
            assert state_payload["foreground_app"] == "com.webos.app.home"

            # 4. launch with content_target: forwards params.contentTarget.
            launch_res = await session.call_tool(
                "launch",
                {
                    "app_id": "youtube.leanback.v4",
                    "content_target": "v=dQw4w9WgXcQ",
                },
            )
            assert launch_res.is_error is False
            assert (
                "ssap://system.launcher/launch",
                {
                    "id": "youtube.leanback.v4",
                    "params": {"contentTarget": "v=dQw4w9WgXcQ"},
                },
            ) in sent_requests

            # 5. Batched keys: acquires pointer socket and sends both button frames.
            keys_res = await session.call_tool(
                "keys",
                {"keys": ["DOWN", "ENTER"], "delay_ms": 0, "settle_ms": 0},
            )
            assert keys_res.is_error is False
            keys_payload = _extract_json_text(keys_res.content)
            assert keys_payload == {"keys_sent": True, "count": 2}
            assert pointer_ws.sent == [
                "type:button\nname:DOWN\n\n",
                "type:button\nname:ENTER\n\n",
            ]

            # 6. set_volume(15): sends {"volume": 15}.
            vol_res = await session.call_tool("set_volume", {"level": 15})
            assert vol_res.is_error is False
            assert ("ssap://audio/setVolume", {"volume": 15}) in sent_requests

            # 7. turn_off(): sends ssap://system/turnOff.
            off_res = await session.call_tool("turn_off", {})
            assert off_res.is_error is False
            assert ("ssap://system/turnOff", {}) in sent_requests
            turn_off_completed.set()
            online_ssap_connections[-1].push_close(1008, "Try Again Later (EWS)")
            await asyncio.wait_for(post_turn_off_standby_entered.wait(), timeout=2.0)

            # 8. Standby (1008) after turn_off: get_state returns cached stale values.
            stale_state_res = await session.call_tool("get_state", {})
            assert stale_state_res.is_error is False
            stale_state_payload = _extract_json_text(stale_state_res.content)
            assert stale_state_payload["connected"] is False
            assert stale_state_payload["stale"] is True
            assert stale_state_payload["volume"] == 10
            assert stale_state_payload["muted"] is False
            assert stale_state_payload["sound_output"] == "tv_speaker"
            assert stale_state_payload["foreground_app"] == "com.webos.app.home"
    finally:
        # 9. Clean ASGI lifespan shutdown with zero orphaned asyncio tasks.
        allow_shutdown.set()
        await asyncio.wait_for(lifespan_task, timeout=2.0)

    # sse_starlette spawns one per-loop _shutdown_watcher() singleton polling AppStatus.should_exit.
    orphaned_tasks = [
        task
        for task in (asyncio.all_tasks() - tasks_before)
        if getattr(task.get_coro(), "__qualname__", None) != "_shutdown_watcher"
    ]
    assert orphaned_tasks == []
