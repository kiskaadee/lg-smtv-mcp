"""SSAP WebSocket client and msg['id'] frame multiplexer."""

import asyncio
import inspect
import json
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, cast

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.protocols import (
    WebSocketClosedError,
    WebSocketConnectFn,
    WebSocketTransport,
)

PERMISSIONS: tuple[str, ...] = (
    "LAUNCH",
    "CONTROL_AUDIO",
    "CONTROL_DISPLAY",
    "CONTROL_POWER",
    "CONTROL_INPUT_TEXT",
    "CONTROL_INPUT_MEDIA_PLAYBACK",
    "CONTROL_INPUT_JOYSTICK",
    "CONTROL_INPUT_TV",
    "CONTROL_TV_MEDIA",
    "CONTROL_MOUSE_AND_KEYBOARD",
    "READ_INSTALLED_APPS",
    "READ_RUNNING_APPS",
    "READ_INPUT_DEVICE_LIST",
    "READ_TV_CHANNEL_LIST",
    "READ_POWER_STATE",
    "WRITE_NOTIFICATION_TOAST",
)


def build_ssl_context() -> ssl.SSLContext:
    """Create an SSL context accepting the TV's self-signed WSS certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def build_register_payload(
    client_key: str | None,
    *,
    force_pairing: bool = False,
    msg_id: str = "register_0",
) -> dict[str, Any]:
    """Build the SSAP register handshake payload with all required permissions."""
    payload: dict[str, Any] = {
        "forcePairing": force_pairing,
        "pairingType": "PROMPT",
        "manifest": {
            "manifestVersion": 1,
            "appVersion": "0.1.0",
            "permissions": list(PERMISSIONS),
            "localizedAppNames": {"": "lg-smtv-mcp"},
        },
    }
    if client_key and not force_pairing:
        payload["client-key"] = client_key
    return {"type": "register", "id": msg_id, "payload": payload}


def _to_closed_error(exc: ConnectionClosed) -> WebSocketClosedError:
    if exc.rcvd is not None:
        return WebSocketClosedError(exc.rcvd.code, exc.rcvd.reason)
    return WebSocketClosedError(1006, str(exc))


class _WebsocketsAdapter:
    """Adapt websockets.asyncio.client.ClientConnection to WebSocketTransport."""

    def __init__(self, conn: ClientConnection) -> None:
        self._conn = conn

    async def send(self, data: str) -> None:
        try:
            await self._conn.send(data)
        except ConnectionClosed as exc:
            raise _to_closed_error(exc) from exc

    async def recv(self) -> str:
        try:
            frame = await self._conn.recv()
        except ConnectionClosed as exc:
            raise _to_closed_error(exc) from exc
        if isinstance(frame, bytes):
            return frame.decode("utf-8", errors="replace")
        return frame

    async def close(self) -> None:
        await self._conn.close()


async def websockets_connect(url: str) -> WebSocketTransport:
    """Connect to an LG webOS SSAP or pointer WebSocket endpoint."""
    ssl_ctx = build_ssl_context() if url.startswith("wss://") else None
    conn = await websockets.connect(
        url,
        ssl=ssl_ctx,
        open_timeout=3.5,
        ping_interval=10,
        ping_timeout=5,
    )
    return _WebsocketsAdapter(conn)


def _is_permission_error(text: str) -> bool:
    lowered = text.lower()
    return "401" in lowered or "insufficient permissions" in lowered


def _extract_error_text(msg: dict[str, Any]) -> str:
    payload = msg.get("payload")
    if isinstance(payload, dict):
        payload_dict = cast(dict[str, Any], payload)
        err_text = payload_dict.get("errorText") or payload_dict.get("errorCode")
        if err_text:
            return str(err_text)
    if msg.get("error"):
        return str(msg["error"])
    return "SSAP request failed"


class SsapClient:
    """Persistent SSAP WebSocket client with msg['id'] request/subscribe demultiplexing."""

    def __init__(
        self,
        url: str,
        connect_fn: WebSocketConnectFn = websockets_connect,
        *,
        on_disconnect: Callable[[ErrorCode], None] | None = None,
    ) -> None:
        self._url = url
        self._connect_fn = connect_fn
        self._on_disconnect = on_disconnect
        self._ws: WebSocketTransport | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._subscriptions: dict[str, Callable[[dict[str, Any]], Awaitable[None] | None]] = {}
        self._next_id = 1
        self._closing = False

    async def connect_and_register(self, client_key: str | None) -> str:
        """Connect, perform SSAP register handshake, and start the reader loop."""
        await self.close()
        self._closing = False

        try:
            ws = await self._connect_fn(self._url)
        except (TimeoutError, OSError, WebSocketClosedError) as exc:
            raise TvError(ErrorCode.TV_OFFLINE_STANDBY, f"Failed to connect to TV: {exc}") from exc

        self._ws = ws
        try:
            await ws.send(json.dumps(build_register_payload(client_key)))
            while True:
                raw = await ws.recv()
                msg_raw: Any = json.loads(raw)
                if not isinstance(msg_raw, dict):
                    continue
                msg = cast(dict[str, Any], msg_raw)
                msg_type = msg.get("type")
                payload_raw = msg.get("payload")
                payload = cast(dict[str, Any], payload_raw) if isinstance(payload_raw, dict) else {}

                if msg_type == "registered":
                    resolved_key = str(payload.get("client-key") or client_key or "")
                    self._reader_task = asyncio.create_task(self._reader_loop())
                    return resolved_key

                if msg_type == "response" and payload.get("pairingType") == "PROMPT":
                    await self._safe_close_ws(ws)
                    self._ws = None
                    raise TvError(
                        ErrorCode.PAIRING_REQUIRED,
                        "TV requested interactive pairing prompt",
                    )

                if msg_type == "error":
                    await self._safe_close_ws(ws)
                    self._ws = None
                    err_text = _extract_error_text(msg)
                    code = (
                        ErrorCode.PAIRING_REQUIRED
                        if _is_permission_error(err_text) or "pairing" in err_text.lower()
                        else ErrorCode.COMMAND_FAILED
                    )
                    raise TvError(code, err_text)
        except TvError:
            raise
        except (TimeoutError, OSError, WebSocketClosedError, json.JSONDecodeError) as exc:
            await self._safe_close_ws(ws)
            self._ws = None
            raise TvError(
                ErrorCode.TV_OFFLINE_STANDBY,
                f"SSAP handshake interrupted: {exc}",
            ) from exc

    async def request(self, uri: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a one-shot SSAP request and await its matched response payload."""
        ws = self._ws
        if ws is None:
            raise TvError(ErrorCode.TV_OFFLINE_STANDBY, "SSAP socket is not connected")

        msg_id = f"req_{self._next_id}"
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = fut

        frame = json.dumps(
            {
                "type": "request",
                "id": msg_id,
                "uri": uri,
                "payload": payload or {},
            }
        )
        try:
            await ws.send(frame)
        except (WebSocketClosedError, OSError) as exc:
            self._pending.pop(msg_id, None)
            if not fut.done():
                fut.cancel()
            raise TvError(ErrorCode.TV_OFFLINE_STANDBY, f"SSAP send failed: {exc}") from exc

        msg = await fut
        return self._unwrap_response(msg)

    async def subscribe(
        self,
        uri: str,
        callback: Callable[[dict[str, Any]], Awaitable[None] | None],
    ) -> None:
        """Subscribe to an SSAP endpoint and route push updates to callback."""
        ws = self._ws
        if ws is None:
            raise TvError(ErrorCode.TV_OFFLINE_STANDBY, "SSAP socket is not connected")

        msg_id = f"sub_{self._next_id}"
        self._next_id += 1
        self._subscriptions[msg_id] = callback

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = fut

        frame = json.dumps({"type": "subscribe", "id": msg_id, "uri": uri, "payload": {}})
        try:
            await ws.send(frame)
        except (WebSocketClosedError, OSError) as exc:
            self._pending.pop(msg_id, None)
            self._subscriptions.pop(msg_id, None)
            if not fut.done():
                fut.cancel()
            raise TvError(ErrorCode.TV_OFFLINE_STANDBY, f"SSAP subscribe failed: {exc}") from exc

        msg = await fut
        payload = self._unwrap_response(msg)
        await self._invoke_callback(callback, payload)

    async def close(self) -> None:
        """Close the SSAP transport and cancel the background reader loop."""
        self._closing = True
        ws = self._ws
        self._ws = None
        if ws is not None:
            await self._safe_close_ws(ws)

        task = self._reader_task
        self._reader_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        self._subscriptions.clear()

    async def _reader_loop(self) -> None:
        ws = self._ws
        disconnect_code = ErrorCode.TV_OFFLINE_STANDBY
        try:
            while ws is not None:
                raw = await ws.recv()
                msg_raw: Any = json.loads(raw)
                if not isinstance(msg_raw, dict):
                    continue
                msg = cast(dict[str, Any], msg_raw)
                msg_id = msg.get("id")
                if not isinstance(msg_id, str):
                    continue

                if msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                elif msg_id in self._subscriptions:
                    if msg.get("type") == "error":
                        err_text = _extract_error_text(msg)
                        if _is_permission_error(err_text):
                            disconnect_code = ErrorCode.PAIRING_REQUIRED
                            break
                        continue
                    payload = msg.get("payload")
                    if isinstance(payload, dict):
                        payload_dict = cast(dict[str, Any], payload)
                        await self._invoke_callback(self._subscriptions[msg_id], payload_dict)
        except WebSocketClosedError, OSError, json.JSONDecodeError, asyncio.CancelledError:
            pass
        finally:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(
                        TvError(ErrorCode.TV_OFFLINE_STANDBY, "SSAP connection closed")
                    )
            self._pending.clear()
            if ws is not None:
                await self._safe_close_ws(ws)
            if self._ws is ws:
                self._ws = None
            if not self._closing and self._on_disconnect is not None:
                self._on_disconnect(disconnect_code)

    def _unwrap_response(self, msg: dict[str, Any]) -> dict[str, Any]:
        if msg.get("type") == "error":
            err_text = _extract_error_text(msg)
            code = (
                ErrorCode.PAIRING_REQUIRED
                if _is_permission_error(err_text)
                else ErrorCode.COMMAND_FAILED
            )
            raise TvError(code, err_text)

        payload_raw = msg.get("payload")
        if not isinstance(payload_raw, dict):
            return {}
        payload = cast(dict[str, Any], payload_raw)
        if payload.get("returnValue") is False:
            err_text = _extract_error_text(msg)
            code = (
                ErrorCode.PAIRING_REQUIRED
                if _is_permission_error(err_text)
                else ErrorCode.COMMAND_FAILED
            )
            raise TvError(code, err_text)
        return payload

    async def _invoke_callback(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None] | None],
        payload: dict[str, Any],
    ) -> None:
        result = callback(payload)
        if inspect.isawaitable(result):
            await result

    async def _safe_close_ws(self, ws: WebSocketTransport) -> None:
        with suppress(Exception):
            await ws.close()
