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
    def __init__(
        self,
        *,
        error: TvError | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.closed_count = 0
        self.error = error
        self.gate = gate
        self.calls = 0

    async def capture_frame(
        self,
        ssap_request_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> CaptureResult:
        _ = ssap_request_fn
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
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


async def test_manager_ui_lock_times_out_with_lock_timeout(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    gate = asyncio.Event()
    capturer = _FakeCapturer(gate=gate)

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport()

    mgr = TvManager(
        cfg,
        connect_fn=connect_fn,
        capture_client=capturer,
        backoff_steps=(10.0,),
        ui_lock_timeout=0.02,
    )
    await mgr.start()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)

    holder = asyncio.create_task(mgr.screenshot())
    await asyncio.sleep(0.01)
    try:
        with pytest.raises(TvError) as exc_info:
            await mgr.screenshot()
        assert exc_info.value.code is ErrorCode.LOCK_TIMEOUT
    finally:
        gate.set()
        await holder
        await mgr.close()


@pytest.mark.parametrize(
    ("keys", "delay_ms", "settle_ms"),
    [
        ([], 250, 0),
        (["UP"] * 21, 250, 0),
        (["UP"], -1, 0),
        (["UP"], 501, 0),
        (["UP"], 0, -1),
        (["UP"], 0, 2001),
    ],
)
async def test_manager_send_keys_validates_bounds_with_invalid_argument(
    tmp_path: Path,
    keys: list[str],
    delay_ms: int,
    settle_ms: int,
) -> None:
    cfg = _make_config(tmp_path)

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport()

    mgr = TvManager(cfg, connect_fn=connect_fn, backoff_steps=(10.0,))
    await mgr.start()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)

    with pytest.raises(TvError) as exc_info:
        await mgr.send_keys(keys, delay_ms=delay_ms, settle_ms=settle_ms)
    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    await mgr.close()


async def test_manager_send_keys_capture_returns_partial_success_when_capture_unavailable(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    pointer_ws = FakeWebSocketTransport()
    ssap_ws = _make_online_transport(
        on_request=lambda msg, ws: ws.push_json(
            {
                "type": "response",
                "id": msg["id"],
                "payload": {"returnValue": True, "socketPath": "wss://192.0.2.10:3001/ptr"},
            }
        )
    )

    async def connect_fn(url: str) -> FakeWebSocketTransport:
        return pointer_ws if "/ptr" in url else ssap_ws

    # Case 1: capture_client is None
    mgr_none = TvManager(cfg, connect_fn=connect_fn, capture_client=None, backoff_steps=(10.0,))
    await mgr_none.start()
    for _ in range(30):
        if mgr_none.get_state().connected:
            break
        await asyncio.sleep(0.01)

    res_none = await mgr_none.send_keys(["UP", "ENTER"], delay_ms=0, settle_ms=0, capture=True)
    assert res_none.keys_sent is True
    assert res_none.count == 2
    assert res_none.capture_error is not None
    assert "SCREENSHOT_UNAVAILABLE" in res_none.capture_error
    await mgr_none.close()

    # Case 2: capture_client raises SCREENSHOT_UNAVAILABLE
    pointer_ws2 = FakeWebSocketTransport()
    ssap_ws2 = _make_online_transport(
        on_request=lambda msg, ws: ws.push_json(
            {
                "type": "response",
                "id": msg["id"],
                "payload": {"returnValue": True, "socketPath": "wss://192.0.2.10:3001/ptr"},
            }
        )
    )

    async def connect_fn2(url: str) -> FakeWebSocketTransport:
        return pointer_ws2 if "/ptr" in url else ssap_ws2

    failing_capturer = _FakeCapturer(
        error=TvError(ErrorCode.SCREENSHOT_UNAVAILABLE, "Dev Mode expired")
    )
    mgr_err = TvManager(
        cfg, connect_fn=connect_fn2, capture_client=failing_capturer, backoff_steps=(10.0,)
    )
    await mgr_err.start()
    for _ in range(30):
        if mgr_err.get_state().connected:
            break
        await asyncio.sleep(0.01)

    res_err = await mgr_err.send_keys(["DOWN"], delay_ms=0, settle_ms=0, capture=True)
    assert res_err.keys_sent is True
    assert res_err.count == 1
    assert res_err.capture_error == "SCREENSHOT_UNAVAILABLE: Dev Mode expired"
    await mgr_err.close()


async def test_manager_screenshot_raises_unavailable_when_capture_client_is_none(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport()

    mgr = TvManager(cfg, connect_fn=connect_fn, capture_client=None, backoff_steps=(10.0,))
    await mgr.start()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)

    with pytest.raises(TvError) as exc_info:
        await mgr.screenshot()
    assert exc_info.value.code is ErrorCode.SCREENSHOT_UNAVAILABLE
    await mgr.close()


async def test_manager_set_volume_enforces_max_volume_guardrail(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, max_volume=35)
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    def on_request(msg: dict[str, Any], ws: FakeWebSocketTransport) -> None:
        sent_requests.append((msg["uri"], msg["payload"]))
        ws.push_json({"type": "response", "id": msg["id"], "payload": {"returnValue": True}})

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport(on_request=on_request)

    mgr = TvManager(cfg, connect_fn=connect_fn, backoff_steps=(10.0,))
    await mgr.start()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)

    for bad_level in (-1, 36):
        with pytest.raises(TvError) as exc_info:
            await mgr.set_volume(bad_level)
        assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT

    await mgr.set_volume(20)
    assert sent_requests == [("ssap://audio/setVolume", {"volume": 20})]
    await mgr.close()


async def test_manager_mvp_ssap_controls_launch_list_mute_and_turn_off(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    def on_request(msg: dict[str, Any], ws: FakeWebSocketTransport) -> None:
        uri = msg["uri"]
        sent_requests.append((uri, msg["payload"]))
        if uri == "ssap://com.webos.applicationManager/listLaunchPoints":
            ws.push_json(
                {
                    "type": "response",
                    "id": msg["id"],
                    "payload": {
                        "returnValue": True,
                        "launchPoints": [{"id": "netflix", "title": "Netflix"}],
                    },
                }
            )
        else:
            ws.push_json({"type": "response", "id": msg["id"], "payload": {"returnValue": True}})

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport(on_request=on_request)

    mgr = TvManager(cfg, connect_fn=connect_fn, backoff_steps=(10.0,))
    await mgr.start()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)

    await mgr.launch_app("youtube.leanback.v4", content_target="v=abc&t=10s")
    apps = await mgr.list_apps()
    await mgr.set_mute(True)
    await mgr.turn_off()

    assert apps == [{"id": "netflix", "title": "Netflix"}]
    assert sent_requests == [
        (
            "ssap://system.launcher/launch",
            {"id": "youtube.leanback.v4", "params": {"contentTarget": "v=abc&t=10s"}},
        ),
        ("ssap://com.webos.applicationManager/listLaunchPoints", {}),
        ("ssap://audio/setMute", {"mute": True}),
        ("ssap://system/turnOff", {}),
    ]
    await mgr.close()


async def test_manager_wake_pairing_failure_clears_wake_requested_and_respects_backoff(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    connect_attempts = 0
    return_prompt = False

    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            if return_prompt:
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg["id"],
                        "payload": {"pairingType": "PROMPT", "returnValue": True},
                    }
                )
            else:
                ws.push_close(1008, "Try Again Later (EWS)")

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        nonlocal connect_attempts
        connect_attempts += 1
        return FakeWebSocketTransport(on_send=on_send)

    mgr = TvManager(
        cfg,
        connect_fn=connect_fn,
        wol_fn=_noop_wol,
        backoff_steps=(1.0,),
        wake_poll_interval=0.01,
        wake_timeout=1.0,
    )
    await mgr.start()
    await asyncio.sleep(0.02)
    assert mgr.state is ConnectionState.STANDBY_OFFLINE

    return_prompt = True
    with pytest.raises(TvError) as exc_info:
        await mgr.wake()
    assert exc_info.value.code is ErrorCode.PAIRING_REQUIRED

    attempts_after_wake = connect_attempts
    await asyncio.sleep(0.05)
    assert connect_attempts == attempts_after_wake
    await mgr.close()


async def test_manager_type_text_bounds_and_capture_partial_success(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    def on_request(msg: dict[str, Any], ws: FakeWebSocketTransport) -> None:
        sent_requests.append((msg["uri"], msg["payload"]))
        ws.push_json({"type": "response", "id": msg["id"], "payload": {"returnValue": True}})

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport(on_request=on_request)

    failing_capturer = _FakeCapturer(
        error=TvError(ErrorCode.SCREENSHOT_UNAVAILABLE, "Dev Mode expired")
    )
    mgr = TvManager(
        cfg,
        connect_fn=connect_fn,
        capture_client=failing_capturer,
        backoff_steps=(10.0,),
    )
    await mgr.start()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)

    for bad_text in ("", "x" * 501):
        with pytest.raises(TvError) as exc_info:
            await mgr.type_text(bad_text)
        assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT

    res = await mgr.type_text("anime", send_enter=True, capture=True)
    assert res.text_sent is True
    assert res.enter_sent is True
    assert res.capture_error == "SCREENSHOT_UNAVAILABLE: Dev Mode expired"
    assert sent_requests == [
        ("ssap://com.webos.service.ime/insertText", {"text": "anime", "replace": 0}),
        ("ssap://com.webos.service.ime/sendEnterKey", {}),
    ]
    await mgr.close()


async def test_manager_list_inputs_and_switch_input(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    def on_request(msg: dict[str, Any], ws: FakeWebSocketTransport) -> None:
        uri = msg["uri"]
        sent_requests.append((uri, msg["payload"]))
        if uri == "ssap://tv/getExternalInputList":
            ws.push_json(
                {
                    "type": "response",
                    "id": msg["id"],
                    "payload": {
                        "returnValue": True,
                        "devices": [{"id": "HDMI_1", "label": "HDMI 1", "connected": True}],
                    },
                }
            )
        else:
            ws.push_json({"type": "response", "id": msg["id"], "payload": {"returnValue": True}})

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport(on_request=on_request)

    mgr = TvManager(cfg, connect_fn=connect_fn, backoff_steps=(10.0,))
    await mgr.start()
    for _ in range(30):
        if mgr.get_state().connected:
            break
        await asyncio.sleep(0.01)

    inputs = await mgr.list_inputs()
    await mgr.switch_input("HDMI_1")

    assert inputs == [{"id": "HDMI_1", "label": "HDMI 1", "connected": True}]
    assert sent_requests == [
        ("ssap://tv/getExternalInputList", {}),
        ("ssap://tv/switchInput", {"inputId": "HDMI_1"}),
    ]
    await mgr.close()


async def test_manager_raw_ssap_respects_enable_raw_ssap_gate(tmp_path: Path) -> None:
    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport(
            on_request=lambda msg, ws: ws.push_json(
                {"type": "response", "id": msg["id"], "payload": {"returnValue": True, "ok": 1}}
            )
        )

    mgr_disabled = TvManager(
        _make_config(tmp_path, enable_raw_ssap=False),
        connect_fn=connect_fn,
        backoff_steps=(10.0,),
    )
    await mgr_disabled.start()
    for _ in range(30):
        if mgr_disabled.get_state().connected:
            break
        await asyncio.sleep(0.01)

    with pytest.raises(TvError) as exc_info:
        await mgr_disabled.raw_ssap("ssap://system/getSystemInfo")
    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    await mgr_disabled.close()

    mgr_enabled = TvManager(
        _make_config(tmp_path, enable_raw_ssap=True),
        connect_fn=connect_fn,
        backoff_steps=(10.0,),
    )
    await mgr_enabled.start()
    for _ in range(30):
        if mgr_enabled.get_state().connected:
            break
        await asyncio.sleep(0.01)

    resp = await mgr_enabled.raw_ssap("ssap://system/getSystemInfo", {"x": 1})
    assert resp == {"returnValue": True, "ok": 1}
    await mgr_enabled.close()
