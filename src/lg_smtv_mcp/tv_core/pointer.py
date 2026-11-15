"""Pointer input WebSocket client for LG webOS D-pad and remote buttons."""

from collections.abc import Awaitable, Callable
from typing import Any

from lg_smtv_mcp.tv_core.protocols import WebSocketConnectFn
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


class PointerClient:
    """Manages the persistent webOS pointer input socket and button frame formatting."""

    def __init__(
        self,
        ssap_request_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
        connect_fn: WebSocketConnectFn = websockets_connect,
    ) -> None:
        raise NotImplementedError

    async def send_buttons(self, keys: list[str], delay_ms: int = 250) -> int:
        """Validate, normalize, and send a sequence of button frames."""
        raise NotImplementedError

    async def close(self) -> None:
        """Close the pointer WebSocket transport if open."""
        raise NotImplementedError
