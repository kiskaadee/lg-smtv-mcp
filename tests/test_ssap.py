import asyncio
import json
from typing import Any

import pytest

from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.protocols import WebSocketClosedError
from lg_smtv_mcp.tv_core.ssap import PERMISSIONS, SsapClient
from tests.conftest import FakeWebSocketTransport


async def test_ssap_register_sends_all_permissions_even_with_saved_client_key() -> None:
    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            ws.push_json(
                {
                    "type": "registered",
                    "id": msg["id"],
                    "payload": {"client-key": "saved-key-123"},
                }
            )

    transport = FakeWebSocketTransport(on_send=on_send)

    async def connect(_url: str) -> FakeWebSocketTransport:
        return transport

    client = SsapClient("wss://192.0.2.10:3001", connect_fn=connect)
    key = await client.connect_and_register("saved-key-123")
    await client.close()

    assert key == "saved-key-123"
    reg_msg = json.loads(transport.sent[0])
    assert reg_msg["type"] == "register"
    assert reg_msg["payload"]["client-key"] == "saved-key-123"
    assert len(PERMISSIONS) == 16
    assert reg_msg["payload"]["manifest"]["permissions"] == list(PERMISSIONS)


async def test_ssap_request_demuxes_interleaved_subscribe_push_frame() -> None:
    sub_id_holder: list[str] = []

    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            ws.push_json({"type": "registered", "id": msg["id"], "payload": {"client-key": "k"}})
        elif msg["type"] == "subscribe":
            sub_id_holder.append(msg["id"])
            ws.push_json(
                {
                    "type": "response",
                    "id": msg["id"],
                    "payload": {"subscribed": True, "returnValue": True, "volume": 10},
                }
            )
        elif msg["type"] == "request":
            # Push a subscribe update frame BEFORE the request response frame.
            ws.push_json(
                {
                    "type": "response",
                    "id": sub_id_holder[0],
                    "payload": {"returnValue": True, "volume": 10, "mute": True},
                }
            )
            ws.push_json(
                {
                    "type": "response",
                    "id": msg["id"],
                    "payload": {"returnValue": True, "muteApplied": True},
                }
            )

    transport = FakeWebSocketTransport(on_send=on_send)

    async def connect(_url: str) -> FakeWebSocketTransport:
        return transport

    client = SsapClient("wss://192.0.2.10:3001", connect_fn=connect)
    await client.connect_and_register("k")

    updates: list[dict[str, Any]] = []
    await client.subscribe("ssap://audio/getStatus", updates.append)
    resp = await client.request("ssap://audio/setMute", {"mute": True})
    await asyncio.sleep(0)
    await client.close()

    assert resp == {"returnValue": True, "muteApplied": True}
    assert updates[-1]["mute"] is True


async def test_ssap_connect_maps_1008_and_timeout_to_tv_offline_standby() -> None:
    transport_1008 = FakeWebSocketTransport()
    transport_1008.push_close(1008, "Try Again Later (EWS)")

    async def connect_1008(_url: str) -> FakeWebSocketTransport:
        return transport_1008

    client_1008 = SsapClient("wss://192.0.2.10:3001", connect_fn=connect_1008)
    with pytest.raises(TvError) as exc_1008:
        await client_1008.connect_and_register("k")
    assert exc_1008.value.code is ErrorCode.TV_OFFLINE_STANDBY

    async def connect_timeout(_url: str) -> FakeWebSocketTransport:
        raise TimeoutError("handshake timed out")

    client_timeout = SsapClient("wss://192.0.2.10:3001", connect_fn=connect_timeout)
    with pytest.raises(TvError) as exc_timeout:
        await client_timeout.connect_and_register("k")
    assert exc_timeout.value.code is ErrorCode.TV_OFFLINE_STANDBY


async def test_ssap_register_maps_prompt_to_pairing_required() -> None:
    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            ws.push_json(
                {
                    "type": "response",
                    "id": msg["id"],
                    "payload": {"pairingType": "PROMPT", "returnValue": True},
                }
            )

    transport = FakeWebSocketTransport(on_send=on_send)

    async def connect(_url: str) -> FakeWebSocketTransport:
        return transport

    client = SsapClient("wss://192.0.2.10:3001", connect_fn=connect)
    with pytest.raises(TvError) as exc_info:
        await client.connect_and_register(None)
    assert exc_info.value.code is ErrorCode.PAIRING_REQUIRED
    assert transport.closed is True


async def test_ssap_request_maps_return_value_false_and_401_to_domain_errors() -> None:
    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            ws.push_json({"type": "registered", "id": msg["id"], "payload": {"client-key": "k"}})
        elif msg["type"] == "request":
            if msg["uri"] == "ssap://fail/cmd":
                ws.push_json(
                    {
                        "type": "response",
                        "id": msg["id"],
                        "payload": {"returnValue": False, "errorText": "bad target"},
                    }
                )
            elif msg["uri"] == "ssap://settings/getSystemSettings":
                ws.push_json(
                    {
                        "type": "error",
                        "id": msg["id"],
                        "error": "401 insufficient permissions",
                        "payload": {"returnValue": False},
                    }
                )

    transport = FakeWebSocketTransport(on_send=on_send)

    async def connect(_url: str) -> FakeWebSocketTransport:
        return transport

    client = SsapClient("wss://192.0.2.10:3001", connect_fn=connect)
    await client.connect_and_register("k")

    with pytest.raises(TvError) as exc_cmd:
        await client.request("ssap://fail/cmd")
    assert exc_cmd.value.code is ErrorCode.COMMAND_FAILED

    with pytest.raises(TvError) as exc_perm:
        await client.request("ssap://settings/getSystemSettings")
    assert exc_perm.value.code is ErrorCode.PAIRING_REQUIRED

    await client.close()


async def test_ssap_reader_loop_finally_fails_pending_futures_with_standby_error() -> None:
    def on_send(raw: str, ws: FakeWebSocketTransport) -> None:
        msg = json.loads(raw)
        if msg["type"] == "register":
            ws.push_json({"type": "registered", "id": msg["id"], "payload": {"client-key": "k"}})
        elif msg["type"] == "request":
            # Drop the socket instead of answering the request.
            ws.push_error(WebSocketClosedError(1008, "Try Again Later (EWS)"))

    transport = FakeWebSocketTransport(on_send=on_send)

    async def connect(_url: str) -> FakeWebSocketTransport:
        return transport

    disconnected: list[ErrorCode] = []
    client = SsapClient(
        "wss://192.0.2.10:3001",
        connect_fn=connect,
        on_disconnect=disconnected.append,
    )
    await client.connect_and_register("k")

    with pytest.raises(TvError) as exc_info:
        await client.request("ssap://audio/getStatus")
    assert exc_info.value.code is ErrorCode.TV_OFFLINE_STANDBY
    await asyncio.sleep(0)
    assert disconnected == [ErrorCode.TV_OFFLINE_STANDBY]
    await client.close()
