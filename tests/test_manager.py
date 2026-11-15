import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.manager import ConnectionState, TvManager
from lg_smtv_mcp.tv_core.protocols import CaptureResult, WebSocketClosedError
from tests.conftest import FakeWebSocketTransport


def _make_config(tmp_path: Path, **overrides: Any) -> TvConfig:
    key_path = tmp_path / ".client_key.json"
    if "client_key_path" not in overrides:
        key_path.write_text(json.dumps({"192.0.2.10": "saved-key"}), encoding="utf-8")
    defaults: dict[str, Any] = {
        "tv_ip": "192.0.2.10",
        "tv_mac": "00:11:22:33:44:55",
        "client_key_path": key_path,
        "max_volume": 40,
    }
    defaults.update(overrides)
    return TvConfig(**defaults)


class _FakeCapturer:
    def __init__(self, *, error: TvError | None = None) -> None:
        self.closed_count = 0
        self.error = error
        self.calls = 0

    async def capture_frame(
        self,
        ssap_request_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> CaptureResult:
        _ = ssap_request_fn
        self.calls += 1
        if self.error is not None:
            raise self.error
        return CaptureResult(
            image_bytes=b"\xff\xd8\xff\xe0",
            frame_sha256="deadbeef",
            changed_since_last=True,
            size_bytes=4,
        )

    async def close(self) -> None:
        self.closed_count += 1


def _make_online_transport(
    *,
    volume: int = 22,
    mute: bool = False,
    sound_output: str = "tv_speaker",
    app_id: str = "com.webos.app.livetv",
    on_request: Callable[[dict[str, Any], FakeWebSocketTransport], None] | None = None,
) -> FakeWebSocketTransport:
    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        msg_type = msg.get("type")
        if msg_type == "register":
            ws.push_json({"type": "registered", "id": msg["id"], "payload": {"client-key": "k"}})
        elif msg_type == "subscribe":
            uri = msg["uri"]
            if uri == "ssap://audio/getStatus":
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg["id"],
                        "payload": {
                            "returnValue": True,
                            "subscribed": True,
                            "volume": volume,
                            "mute": mute,
                            "volumeStatus": {"soundOutput": sound_output},
                        },
                    }
                )
            elif uri == "ssap://com.webos.applicationManager/getForegroundAppInfo":
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg["id"],
                        "payload": {
                            "returnValue": True,
                            "subscribed": True,
                            "appId": app_id,
                        },
                    }
                )
        elif msg_type == "request" and on_request is not None:
            on_request(msg, ws)

    return FakeWebSocketTransport(on_send=on_send)


async def test_manager_uses_stepped_reconnect_backoff_and_tears_down_capture_on_standby(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_config(tmp_path)
    capturer = _FakeCapturer()
    wait_timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(aw: Any, timeout: float | None = None) -> Any:
        if timeout is not None and timeout >= 2.0:
            wait_timeouts.append(timeout)
            return await real_wait_for(aw, timeout=0.005)
        return await real_wait_for(aw, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", spy_wait_for)

    attempts = 0

    async def connect_standby(_url: str) -> FakeWebSocketTransport:
        nonlocal attempts
        attempts += 1
        ws = FakeWebSocketTransport()
        ws.push_close(1008, "Try Again Later (EWS)")
        return ws

    mgr = TvManager(
        cfg,
        connect_fn=connect_standby,
        capture_client=capturer,
        backoff_steps=(2.0, 5.0, 15.0, 30.0),
    )
    await mgr.start()
    for _ in range(50):
        if len(wait_timeouts) >= 5:
            break
        await asyncio.sleep(0.005)
    await mgr.close()

    assert mgr.state is ConnectionState.STANDBY_OFFLINE
    assert wait_timeouts[:5] == [2.0, 5.0, 15.0, 30.0, 30.0]
    assert capturer.closed_count >= 1


async def test_manager_wake_coalesces_concurrent_calls_and_handles_connected_and_pairing(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    wol_calls: list[tuple[str, str, int]] = []
    online = False

    def fake_wol(mac: str, broadcast: str, port: int) -> None:
        wol_calls.append((mac, broadcast, port))

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        if not online:
            ws = FakeWebSocketTransport()
            ws.push_close(1008, "Try Again Later (EWS)")
            return ws
        return _make_online_transport()

    mgr = TvManager(
        cfg,
        connect_fn=connect_fn,
        wol_fn=fake_wol,
        backoff_steps=(10.0,),
        wake_poll_interval=0.01,
        wake_timeout=2.0,
    )
    await mgr.start()
    await asyncio.sleep(0.02)
    assert mgr.state is ConnectionState.STANDBY_OFFLINE

    async def flip_online() -> None:
        nonlocal online
        await asyncio.sleep(0.03)
        online = True

    flipper = asyncio.create_task(flip_online())
    res1, res2 = await asyncio.gather(mgr.wake(), mgr.wake())
    await flipper

    assert len(wol_calls) == 1
    assert res1.status == "connected"
    assert res2.status == "connected"
    assert mgr.state is ConnectionState.CONNECTED

    already = await mgr.wake()
    assert already.status == "already_connected"
    assert already.elapsed_ms == 0
    await mgr.close()

    # PAIRING_REQUIRED fast-fail
    def on_send_prompt(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            ws.push_json(
                {
                    "type": "response",
                    "id": msg["id"],
                    "payload": {"pairingType": "PROMPT", "returnValue": True},
                }
            )

    async def connect_prompt(_url: str) -> FakeWebSocketTransport:
        return FakeWebSocketTransport(on_send=on_send_prompt)

    mgr_pair = TvManager(cfg, connect_fn=connect_prompt, wol_fn=fake_wol, backoff_steps=(10.0,))
    await mgr_pair.start()
    await asyncio.sleep(0.02)
    assert mgr_pair.state is ConnectionState.PAIRING_REQUIRED
    with pytest.raises(TvError) as exc_pair:
        await mgr_pair.wake()
    assert exc_pair.value.code is ErrorCode.PAIRING_REQUIRED
    await mgr_pair.close()


def _noop_wol(_mac: str, _broadcast: str, _port: int) -> None:
    pass


async def test_manager_wake_raises_wake_timeout_when_tv_never_comes_online(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)

    async def connect_standby(_url: str) -> FakeWebSocketTransport:
        ws = FakeWebSocketTransport()
        ws.push_close(1008, "Try Again Later (EWS)")
        return ws

    mgr = TvManager(
        cfg,
        connect_fn=connect_standby,
        wol_fn=_noop_wol,
        backoff_steps=(10.0,),
        wake_poll_interval=0.01,
        wake_timeout=0.05,
    )
    await mgr.start()
    await asyncio.sleep(0.02)

    with pytest.raises(TvError) as exc_info:
        await mgr.wake()
    assert exc_info.value.code is ErrorCode.WAKE_TIMEOUT
    assert mgr.state is ConnectionState.STANDBY_OFFLINE
    await mgr.close()


async def test_manager_reloads_client_key_on_sighup_and_stays_in_pairing_on_bad_json(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / ".client_key.json"
    key_path.write_text("{not valid json", encoding="utf-8")
    cfg = _make_config(tmp_path, client_key_path=key_path)

    def on_send_check_key(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            if msg["payload"].get("client-key") == "fresh-key":
                ws.push_json(
                    {"type": "registered", "id": msg["id"], "payload": {"client-key": "fresh-key"}}
                )
            else:
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg["id"],
                        "payload": {"pairingType": "PROMPT", "returnValue": True},
                    }
                )
        elif msg["type"] == "subscribe":
            ws.push_json(
                {"type": "response", "id": msg["id"], "payload": {"returnValue": True, "volume": 1}}
            )

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return FakeWebSocketTransport(on_send=on_send_check_key)

    mgr = TvManager(cfg, connect_fn=connect_fn, backoff_steps=(10.0,))
    await mgr.start()
    await asyncio.sleep(0.02)
    assert mgr.state is ConnectionState.PAIRING_REQUIRED

    # Reload with malformed JSON stays in PAIRING_REQUIRED.
    mgr.reload_client_key()
    await asyncio.sleep(0.02)
    assert mgr.state is ConnectionState.PAIRING_REQUIRED

    # Write valid JSON and reload -> transitions to CONNECTED.
    key_path.write_text(json.dumps({"192.0.2.10": "fresh-key"}), encoding="utf-8")
    mgr.reload_client_key()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)
    assert mgr.get_state().connected is True
    await mgr.close()


async def test_manager_caches_subscribe_state_and_returns_lock_free_offline_snapshot(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    transport = _make_online_transport(
        volume=27,
        mute=True,
        sound_output="external_arc",
        app_id="netflix",
    )
    connects = 0

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        nonlocal connects
        connects += 1
        if connects == 1:
            return transport
        ws = FakeWebSocketTransport()
        ws.push_close(1008, "Try Again Later (EWS)")
        return ws

    mgr = TvManager(cfg, connect_fn=connect_fn, backoff_steps=(10.0,))
    await mgr.start()
    for _ in range(30):
        if mgr.state is ConnectionState.CONNECTED:
            break
        await asyncio.sleep(0.01)

    snap_online = mgr.get_state()
    assert snap_online.connected is True
    assert snap_online.stale is False
    assert snap_online.volume == 27
    assert snap_online.muted is True
    assert snap_online.sound_output == "external_arc"
    assert snap_online.foreground_app == "netflix"
    assert snap_online.last_updated is not None

    # Drop connection and verify cached values remain available offline.
    transport.push_error(WebSocketClosedError(1008, "Try Again Later (EWS)"))
    for _ in range(30):
        if mgr.state is ConnectionState.STANDBY_OFFLINE:
            break
        await asyncio.sleep(0.01)

    snap_offline = mgr.get_state()
    assert snap_offline.connected is False
    assert snap_offline.stale is True
    assert snap_offline.volume == 27
    assert snap_offline.sound_output == "external_arc"
    assert snap_offline.foreground_app == "netflix"
    await mgr.close()


async def test_manager_transitions_to_pairing_required_on_subscription_401(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)

    def on_send_401(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            ws.push_json({"type": "registered", "id": msg["id"], "payload": {"client-key": "k"}})
        elif msg["type"] == "subscribe":
            ws.push_json(
                {
                    "type": "error",
                    "id": msg["id"],
                    "error": "401 insufficient permissions",
                    "payload": {"returnValue": False},
                }
            )

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return FakeWebSocketTransport(on_send=on_send_401)

    mgr = TvManager(cfg, connect_fn=connect_fn, backoff_steps=(10.0,))
    await mgr.start()
    for _ in range(30):
        if mgr.state is ConnectionState.PAIRING_REQUIRED:
            break
        await asyncio.sleep(0.01)

    assert mgr.state is ConnectionState.PAIRING_REQUIRED
    await mgr.close()
