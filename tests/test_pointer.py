import asyncio
from typing import Any

import pytest

from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.pointer import BUTTON_ALLOWLIST, PointerClient
from tests.conftest import FakeWebSocketTransport


async def test_pointer_normalizes_keys_to_uppercase_and_formats_frames() -> None:
    ws = FakeWebSocketTransport()
    ssap_calls: list[str] = []

    async def fake_ssap(uri: str, _payload: dict[str, Any]) -> dict[str, Any]:
        ssap_calls.append(uri)
        return {"socketPath": "wss://192.0.2.10:3001/pointer/1"}

    async def fake_connect(_url: str) -> FakeWebSocketTransport:
        return ws

    client = PointerClient(fake_ssap, connect_fn=fake_connect)
    count = await client.send_buttons(["up", "Enter", "home"], delay_ms=0)
    await client.close()

    assert count == 3
    assert ssap_calls == ["ssap://com.webos.service.networkinput/getPointerInputSocket"]
    assert ws.sent == [
        "type:button\nname:UP\n\n",
        "type:button\nname:ENTER\n\n",
        "type:button\nname:HOME\n\n",
    ]


@pytest.mark.parametrize("bad_key", ["INVALID_KEY", "UP\nname:HOME", "ENTER\r", ""])
async def test_pointer_rejects_unknown_keys_and_newlines_with_invalid_argument(
    bad_key: str,
) -> None:
    assert "UP" in BUTTON_ALLOWLIST
    ws = FakeWebSocketTransport()

    async def fake_ssap(_uri: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"socketPath": "wss://192.0.2.10:3001/pointer/1"}

    async def fake_connect(_url: str) -> FakeWebSocketTransport:
        return ws

    client = PointerClient(fake_ssap, connect_fn=fake_connect)
    with pytest.raises(TvError) as exc_info:
        await client.send_buttons(["UP", bad_key], delay_ms=0)

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    assert ws.sent == []


async def test_pointer_paces_buttons_by_delay_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWebSocketTransport()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def fake_ssap(_uri: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"socketPath": "wss://192.0.2.10:3001/pointer/1"}

    async def fake_connect(_url: str) -> FakeWebSocketTransport:
        return ws

    client = PointerClient(fake_ssap, connect_fn=fake_connect)
    await client.send_buttons(["LEFT", "RIGHT", "ENTER"], delay_ms=150)
    await client.close()

    assert sleeps == [pytest.approx(0.15), pytest.approx(0.15)]


async def test_pointer_retries_once_on_mid_batch_socket_drop() -> None:
    ws1 = FakeWebSocketTransport()
    ws2 = FakeWebSocketTransport()
    connects = 0

    def drop_after_first(data: str, transport: FakeWebSocketTransport) -> None:
        if "LEFT" in data:
            transport.closed = True

    ws1._on_send = drop_after_first  # pyright: ignore[reportPrivateUsage]

    async def fake_ssap(_uri: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"socketPath": f"wss://192.0.2.10:3001/pointer/{connects + 1}"}

    async def fake_connect(_url: str) -> FakeWebSocketTransport:
        nonlocal connects
        connects += 1
        return ws1 if connects == 1 else ws2

    client = PointerClient(fake_ssap, connect_fn=fake_connect)
    count = await client.send_buttons(["LEFT", "ENTER"], delay_ms=0)
    await client.close()

    assert count == 2
    assert connects == 2
    assert ws1.sent == ["type:button\nname:LEFT\n\n"]
    assert ws2.sent == ["type:button\nname:ENTER\n\n"]
