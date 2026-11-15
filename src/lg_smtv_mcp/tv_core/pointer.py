"""Pointer input WebSocket client for LG webOS D-pad and remote buttons."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.protocols import (
    WebSocketClosedError,
    WebSocketConnectFn,
    WebSocketTransport,
)
from lg_smtv_mcp.tv_core.ssap import websockets_connect

BUTTON_ALLOWLIST: frozenset[str] = frozenset(
    {
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
        "ENTER",
        "BACK",
        "HOME",
        "EXIT",
        "PLAY",
        "PAUSE",
        "STOP",
        "REWIND",
        "FASTFORWARD",
        "RED",
        "GREEN",
        "YELLOW",
        "BLUE",
        "VOLUMEUP",
        "VOLUMEDOWN",
        "MUTE",
        "CHANNELUP",
        "CHANNELDOWN",
        "INFO",
        "GUIDE",
        "MENU",
        "CC",
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
    }
)


def normalize_button_keys(keys: list[str]) -> list[str]:
    """Validate and normalize a sequence of webOS button names."""
    normalized: list[str] = []
    for raw_key in keys:
        if "\r" in raw_key or "\n" in raw_key:
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                f"Button name must not contain newline characters: {raw_key!r}",
            )
        candidate = raw_key.strip().upper()
        if candidate not in BUTTON_ALLOWLIST:
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                f"Unsupported webOS button name: {raw_key!r}",
            )
        normalized.append(candidate)
    return normalized


class PointerClient:
    """Manages the persistent webOS pointer input socket and button frame formatting."""

    def __init__(
        self,
        ssap_request_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
        connect_fn: WebSocketConnectFn = websockets_connect,
    ) -> None:
        self._ssap_request_fn = ssap_request_fn
        self._connect_fn = connect_fn
        self._ws: WebSocketTransport | None = None

    async def _ensure_socket(self) -> WebSocketTransport:
        if self._ws is not None:
            return self._ws
        resp = await self._ssap_request_fn(
            "ssap://com.webos.service.networkinput/getPointerInputSocket",
            {},
        )
        socket_path = resp.get("socketPath")
        if not isinstance(socket_path, str) or not socket_path:
            raise TvError(
                ErrorCode.COMMAND_FAILED,
                "getPointerInputSocket did not return a valid socketPath",
            )
        try:
            self._ws = await self._connect_fn(socket_path)
        except (TimeoutError, OSError, WebSocketClosedError) as exc:
            raise TvError(
                ErrorCode.TV_OFFLINE_STANDBY,
                f"Failed to connect pointer input socket: {exc}",
            ) from exc
        return self._ws

    async def send_buttons(self, keys: list[str], delay_ms: int = 250) -> int:
        """Validate, normalize, and send a sequence of button frames."""
        normalized = normalize_button_keys(keys)
        for index, key in enumerate(normalized):
            frame = f"type:button\nname:{key}\n\n"
            await self._send_frame_with_retry(frame)
            if delay_ms > 0 and index < len(normalized) - 1:
                await asyncio.sleep(delay_ms / 1000.0)
        return len(normalized)

    async def _send_frame_with_retry(self, frame: str) -> None:
        ws = await self._ensure_socket()
        try:
            await ws.send(frame)
            return
        except WebSocketClosedError, OSError:
            await self.close()

        ws = await self._ensure_socket()
        try:
            await ws.send(frame)
        except (WebSocketClosedError, OSError) as exc:
            await self.close()
            raise TvError(
                ErrorCode.TV_OFFLINE_STANDBY,
                f"Pointer input socket failed after retry: {exc}",
            ) from exc

    async def close(self) -> None:
        """Close the pointer WebSocket transport if open."""
        ws = self._ws
        self._ws = None
        if ws is not None:
            with suppress(Exception):
                await ws.close()
