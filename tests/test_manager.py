import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.manager import ConnectionState, TvManager
from lg_smtv_mcp.tv_core.protocols import CaptureResult, WebSocketClosedError
from tests.conftest import FakeWebSocketTransport

POINTER_URL = "wss://192.0.2.10:3001/ptr"


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


def _standby_transport() -> FakeWebSocketTransport:
    ws = FakeWebSocketTransport()
    ws.push_close(1008, "Try Again Later (EWS)")
    return ws


def _pointer_socket_reply(msg: dict[str, Any], ws: FakeWebSocketTransport) -> None:
    ws.push_json(
        {
            "type": "response",
            "id": msg["id"],
            "payload": {"returnValue": True, "socketPath": POINTER_URL},
        }
    )


def _noop_wol(_mac: str, _broadcast: str, _port: int) -> None:
    pass


async def _wait_for(predicate: Callable[[], bool], what: str, timeout: float = 2.0) -> None:
    """Poll predicate until true; fail loudly (instead of silently continuing) on timeout."""
    try:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.005)
    except TimeoutError:
        pytest.fail(f"timed out after {timeout}s waiting for {what}")


async def _wait_for_state(mgr: TvManager, state: ConnectionState) -> None:
    await _wait_for(lambda: mgr.state is state, f"ConnectionState.{state.value}")


@asynccontextmanager
async def _started(
    mgr: TvManager,
    *,
    until: ConnectionState | None = ConnectionState.CONNECTED,
) -> AsyncGenerator[TvManager]:
    """Start mgr, optionally wait for a state, and always close it (even if a test fails)."""
    await mgr.start()
    try:
        if until is not None:
            await _wait_for_state(mgr, until)
        yield mgr
    finally:
        await mgr.close()


# --- Connection lifecycle ---------------------------------------------------------------


async def test_manager_uses_stepped_reconnect_backoff_and_tears_down_capture_on_standby(
    tmp_path: Path,
) -> None:
    capturer = _FakeCapturer()
    waits: list[float] = []

    async def fake_wait(_event: asyncio.Event, timeout: float) -> None:
        waits.append(timeout)
        await asyncio.sleep(0)

    async def connect_standby(_url: str) -> FakeWebSocketTransport:
        return _standby_transport()

    mgr = TvManager(
        _make_config(tmp_path),
        connect_fn=connect_standby,
        capture_client=capturer,
        backoff_steps=(2.0, 5.0, 15.0, 30.0),
        wait_fn=fake_wait,
    )
    async with _started(mgr, until=None):
        await _wait_for(lambda: len(waits) >= 5, "5 backoff waits")

    assert mgr.state is ConnectionState.STANDBY_OFFLINE
    assert waits[:5] == [2.0, 5.0, 15.0, 30.0, 30.0]
    assert capturer.closed_count >= 1


async def test_manager_wake_coalesces_concurrent_calls_into_single_wol_packet(
    tmp_path: Path,
) -> None:
    wol_calls: list[tuple[str, str, int]] = []
    online = False

    def fake_wol(mac: str, broadcast: str, port: int) -> None:
        wol_calls.append((mac, broadcast, port))

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport() if online else _standby_transport()

    mgr = TvManager(
        _make_config(tmp_path),
        connect_fn=connect_fn,
        wol_fn=fake_wol,
        backoff_steps=(10.0,),
        wake_poll_interval=0.01,
        wake_timeout=2.0,
    )
    async with _started(mgr, until=ConnectionState.STANDBY_OFFLINE):

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


async def test_manager_wake_returns_already_connected_without_sending_wol(
    tmp_path: Path,
) -> None:
    wol_calls: list[tuple[str, str, int]] = []

    def fake_wol(mac: str, broadcast: str, port: int) -> None:
        wol_calls.append((mac, broadcast, port))

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport()

    mgr = TvManager(
        _make_config(tmp_path), connect_fn=connect_fn, wol_fn=fake_wol, backoff_steps=(10.0,)
    )
    async with _started(mgr):
        already = await mgr.wake()

    assert already.status == "already_connected"
    assert already.elapsed_ms == 0
    assert wol_calls == []


async def test_manager_wake_fails_fast_with_pairing_required_when_unpaired(
    tmp_path: Path,
) -> None:
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

    mgr = TvManager(
        _make_config(tmp_path), connect_fn=connect_prompt, wol_fn=_noop_wol, backoff_steps=(10.0,)
    )
    async with _started(mgr, until=ConnectionState.PAIRING_REQUIRED):
        with pytest.raises(TvError) as exc_pair:
            await mgr.wake()

    assert exc_pair.value.code is ErrorCode.PAIRING_REQUIRED


async def test_manager_wake_raises_wake_timeout_when_tv_never_comes_online(
    tmp_path: Path,
) -> None:
    async def connect_standby(_url: str) -> FakeWebSocketTransport:
        return _standby_transport()

    mgr = TvManager(
        _make_config(tmp_path),
        connect_fn=connect_standby,
        wol_fn=_noop_wol,
        backoff_steps=(10.0,),
        wake_poll_interval=0.01,
        wake_timeout=0.05,
    )
    async with _started(mgr, until=ConnectionState.STANDBY_OFFLINE):
        with pytest.raises(TvError) as exc_info:
            await mgr.wake()
        assert exc_info.value.code is ErrorCode.WAKE_TIMEOUT
        assert mgr.state is ConnectionState.STANDBY_OFFLINE


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
    async with _started(mgr, until=ConnectionState.PAIRING_REQUIRED):
        # Reload with malformed JSON stays in PAIRING_REQUIRED (absence check: short grace period).
        mgr.reload_client_key()
        await asyncio.sleep(0.02)
        assert mgr.state is ConnectionState.PAIRING_REQUIRED

        # Write valid JSON and reload -> transitions to CONNECTED.
        key_path.write_text(json.dumps({"192.0.2.10": "fresh-key"}), encoding="utf-8")
        mgr.reload_client_key()
        await _wait_for(lambda: mgr.get_state().connected, "connected after key reload")


async def test_manager_caches_subscribe_state_and_returns_lock_free_offline_snapshot(
    tmp_path: Path,
) -> None:
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
        return transport if connects == 1 else _standby_transport()

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr):
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
        await _wait_for_state(mgr, ConnectionState.STANDBY_OFFLINE)

        snap_offline = mgr.get_state()
        assert snap_offline.connected is False
        assert snap_offline.stale is True
        assert snap_offline.volume == 27
        assert snap_offline.sound_output == "external_arc"
        assert snap_offline.foreground_app == "netflix"


async def test_manager_transitions_to_pairing_required_on_subscription_401(
    tmp_path: Path,
) -> None:
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

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr, until=ConnectionState.PAIRING_REQUIRED):
        assert mgr.state is ConnectionState.PAIRING_REQUIRED


async def test_manager_wake_pairing_failure_clears_wake_requested_and_respects_backoff(
    tmp_path: Path,
) -> None:
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
        _make_config(tmp_path),
        connect_fn=connect_fn,
        wol_fn=_noop_wol,
        backoff_steps=(1.0,),
        wake_poll_interval=0.01,
        wake_timeout=1.0,
    )
    async with _started(mgr, until=ConnectionState.STANDBY_OFFLINE):
        return_prompt = True
        with pytest.raises(TvError) as exc_info:
            await mgr.wake()
        assert exc_info.value.code is ErrorCode.PAIRING_REQUIRED

        # Absence check: no busy-loop reconnects within the 1.0s backoff window.
        attempts_after_wake = connect_attempts
        await asyncio.sleep(0.05)
        assert connect_attempts == attempts_after_wake


# --- UI lock & capture ------------------------------------------------------------------


async def test_manager_ui_lock_times_out_with_lock_timeout(tmp_path: Path) -> None:
    gate = asyncio.Event()
    capturer = _FakeCapturer(gate=gate)

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport()

    mgr = TvManager(
        _make_config(tmp_path),
        connect_fn=connect_fn,
        capture_client=capturer,
        backoff_steps=(10.0,),
        ui_lock_timeout=0.02,
    )
    async with _started(mgr):
        holder = asyncio.create_task(mgr.screenshot())
        try:
            # Deterministic: wait until the holder is inside the capture (holding the lock).
            await _wait_for(lambda: capturer.calls == 1, "first screenshot to hold the UI lock")
            with pytest.raises(TvError) as exc_info:
                await mgr.screenshot()
            assert exc_info.value.code is ErrorCode.LOCK_TIMEOUT
        finally:
            gate.set()
            await holder


async def test_manager_screenshot_raises_unavailable_when_capture_client_is_none(
    tmp_path: Path,
) -> None:
    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _make_online_transport()

    mgr = TvManager(
        _make_config(tmp_path), connect_fn=connect_fn, capture_client=None, backoff_steps=(10.0,)
    )
    async with _started(mgr):
        with pytest.raises(TvError) as exc_info:
            await mgr.screenshot()

    assert exc_info.value.code is ErrorCode.SCREENSHOT_UNAVAILABLE


# --- send_keys ---------------------------------------------------------------------------


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
async def test_manager_send_keys_rejects_out_of_bounds_args_without_sending(
    tmp_path: Path,
    keys: list[str],
    delay_ms: int,
    settle_ms: int,
) -> None:
    pointer_ws = FakeWebSocketTransport()
    ssap_ws = _make_online_transport(on_request=_pointer_socket_reply)

    async def connect_fn(url: str) -> FakeWebSocketTransport:
        return pointer_ws if url == POINTER_URL else ssap_ws

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr):
        with pytest.raises(TvError) as exc_info:
            await mgr.send_keys(keys, delay_ms=delay_ms, settle_ms=settle_ms)

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    assert pointer_ws.sent == []


@pytest.mark.parametrize(
    ("keys", "delay_ms", "settle_ms"),
    [
        (["UP"] * 20, 0, 0),
        (["UP"], 0, 0),
        (["UP"], 500, 0),
        (["UP"], 0, 2000),
    ],
)
async def test_manager_send_keys_accepts_inclusive_boundary_values(
    tmp_path: Path,
    keys: list[str],
    delay_ms: int,
    settle_ms: int,
) -> None:
    pointer_ws = FakeWebSocketTransport()
    ssap_ws = _make_online_transport(on_request=_pointer_socket_reply)

    async def connect_fn(url: str) -> FakeWebSocketTransport:
        return pointer_ws if url == POINTER_URL else ssap_ws

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr):
        res = await mgr.send_keys(keys, delay_ms=delay_ms, settle_ms=settle_ms)

    assert res.keys_sent is True
    assert res.count == len(keys)
    assert len(pointer_ws.sent) == len(keys)


@pytest.mark.parametrize(
    ("capturer", "expected_error"),
    [
        (None, "SCREENSHOT_UNAVAILABLE"),
        (
            _FakeCapturer(error=TvError(ErrorCode.SCREENSHOT_UNAVAILABLE, "Dev Mode expired")),
            "SCREENSHOT_UNAVAILABLE: Dev Mode expired",
        ),
    ],
    ids=["no_capture_client", "capture_client_raises"],
)
async def test_manager_send_keys_capture_returns_partial_success_when_capture_unavailable(
    tmp_path: Path,
    capturer: _FakeCapturer | None,
    expected_error: str,
) -> None:
    pointer_ws = FakeWebSocketTransport()
    ssap_ws = _make_online_transport(on_request=_pointer_socket_reply)

    async def connect_fn(url: str) -> FakeWebSocketTransport:
        return pointer_ws if url == POINTER_URL else ssap_ws

    mgr = TvManager(
        _make_config(tmp_path),
        connect_fn=connect_fn,
        capture_client=capturer,
        backoff_steps=(10.0,),
    )
    async with _started(mgr):
        res = await mgr.send_keys(["UP", "ENTER"], delay_ms=0, settle_ms=0, capture=True)

    assert res.keys_sent is True
    assert res.count == 2
    assert res.capture_error is not None
    assert expected_error in res.capture_error
    assert pointer_ws.sent == ["type:button\nname:UP\n\n", "type:button\nname:ENTER\n\n"]


# --- SSAP controls -----------------------------------------------------------------------


def _recording_transport(
    sent_requests: list[tuple[str, dict[str, Any]]],
    replies: dict[str, dict[str, Any]] | None = None,
) -> FakeWebSocketTransport:
    """Online transport that records requests and answers per-URI (default returnValue=True)."""
    replies = replies or {}

    def on_request(msg: dict[str, Any], ws: FakeWebSocketTransport) -> None:
        uri = msg["uri"]
        sent_requests.append((uri, msg["payload"]))
        payload = {"returnValue": True, **replies.get(uri, {})}
        ws.push_json({"type": "response", "id": msg["id"], "payload": payload})

    return _make_online_transport(on_request=on_request)


@pytest.mark.parametrize("level", [0, 20, 35])
async def test_manager_set_volume_accepts_levels_within_inclusive_max_volume(
    tmp_path: Path, level: int
) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests)

    mgr = TvManager(
        _make_config(tmp_path, max_volume=35), connect_fn=connect_fn, backoff_steps=(10.0,)
    )
    async with _started(mgr):
        await mgr.set_volume(level)

    assert sent_requests == [("ssap://audio/setVolume", {"volume": level})]


@pytest.mark.parametrize("level", [-1, 36])
async def test_manager_set_volume_rejects_levels_outside_max_volume_without_sending(
    tmp_path: Path, level: int
) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests)

    mgr = TvManager(
        _make_config(tmp_path, max_volume=35), connect_fn=connect_fn, backoff_steps=(10.0,)
    )
    async with _started(mgr):
        with pytest.raises(TvError) as exc_info:
            await mgr.set_volume(level)

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    assert sent_requests == []


async def test_manager_mvp_ssap_controls_send_expected_uris_and_payloads(tmp_path: Path) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []
    replies = {
        "ssap://com.webos.applicationManager/listLaunchPoints": {
            "launchPoints": [{"id": "netflix", "title": "Netflix"}]
        }
    }

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests, replies)

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr):
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


@pytest.mark.parametrize("bad_text", ["", "x" * 501])
async def test_manager_type_text_rejects_out_of_bounds_length_without_sending(
    tmp_path: Path, bad_text: str
) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests)

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr):
        with pytest.raises(TvError) as exc_info:
            await mgr.type_text(bad_text)

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    assert sent_requests == []


@pytest.mark.parametrize("text", ["a", "x" * 500])
async def test_manager_type_text_accepts_inclusive_boundary_lengths(
    tmp_path: Path, text: str
) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests)

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr):
        res = await mgr.type_text(text, send_enter=False)

    assert res.text_sent is True
    assert res.enter_sent is False
    assert sent_requests == [
        ("ssap://com.webos.service.ime/insertText", {"text": text, "replace": 0})
    ]


async def test_manager_type_text_capture_returns_partial_success_when_capture_fails(
    tmp_path: Path,
) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests)

    failing_capturer = _FakeCapturer(
        error=TvError(ErrorCode.SCREENSHOT_UNAVAILABLE, "Dev Mode expired")
    )
    mgr = TvManager(
        _make_config(tmp_path),
        connect_fn=connect_fn,
        capture_client=failing_capturer,
        backoff_steps=(10.0,),
    )
    async with _started(mgr):
        res = await mgr.type_text("anime", send_enter=True, capture=True)

    assert res.text_sent is True
    assert res.enter_sent is True
    assert res.capture_error == "SCREENSHOT_UNAVAILABLE: Dev Mode expired"
    assert sent_requests == [
        ("ssap://com.webos.service.ime/insertText", {"text": "anime", "replace": 0}),
        ("ssap://com.webos.service.ime/sendEnterKey", {}),
    ]


async def test_manager_list_inputs_and_switch_input_send_expected_uris(tmp_path: Path) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []
    replies = {
        "ssap://tv/getExternalInputList": {
            "devices": [{"id": "HDMI_1", "label": "HDMI 1", "connected": True}]
        }
    }

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests, replies)

    mgr = TvManager(_make_config(tmp_path), connect_fn=connect_fn, backoff_steps=(10.0,))
    async with _started(mgr):
        inputs = await mgr.list_inputs()
        await mgr.switch_input("HDMI_1")

    assert inputs == [{"id": "HDMI_1", "label": "HDMI 1", "connected": True}]
    assert sent_requests == [
        ("ssap://tv/getExternalInputList", {}),
        ("ssap://tv/switchInput", {"inputId": "HDMI_1"}),
    ]


async def test_manager_raw_ssap_rejects_when_disabled_without_sending(tmp_path: Path) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests)

    mgr = TvManager(
        _make_config(tmp_path, enable_raw_ssap=False), connect_fn=connect_fn, backoff_steps=(10.0,)
    )
    async with _started(mgr):
        with pytest.raises(TvError) as exc_info:
            await mgr.raw_ssap("ssap://system/getSystemInfo")

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    assert sent_requests == []


async def test_manager_raw_ssap_forwards_request_when_enabled(tmp_path: Path) -> None:
    sent_requests: list[tuple[str, dict[str, Any]]] = []
    replies = {"ssap://system/getSystemInfo": {"ok": 1}}

    async def connect_fn(_url: str) -> FakeWebSocketTransport:
        return _recording_transport(sent_requests, replies)

    mgr = TvManager(
        _make_config(tmp_path, enable_raw_ssap=True), connect_fn=connect_fn, backoff_steps=(10.0,)
    )
    async with _started(mgr):
        resp = await mgr.raw_ssap("ssap://system/getSystemInfo", {"x": 1})

    assert resp == {"returnValue": True, "ok": 1}
    assert sent_requests == [("ssap://system/getSystemInfo", {"x": 1})]
