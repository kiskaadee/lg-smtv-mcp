"""SSAP WebSocket client and msg['id'] frame multiplexer."""

from collections.abc import Awaitable, Callable
from typing import Any

from lg_smtv_mcp.errors import ErrorCode
from lg_smtv_mcp.tv_core.protocols import WebSocketConnectFn, WebSocketTransport

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


def build_register_payload(
    client_key: str | None,
    *,
    force_pairing: bool = False,
    msg_id: str = "register_0",
) -> dict[str, Any]:
    """Build the SSAP register handshake payload."""
    raise NotImplementedError


async def websockets_connect(url: str) -> WebSocketTransport:
    """Connect to an LG webOS SSAP or pointer WebSocket endpoint."""
    raise NotImplementedError


class SsapClient:
    """Persistent SSAP WebSocket client with msg['id'] request/subscribe demultiplexing."""

    def __init__(
        self,
        url: str,
        connect_fn: WebSocketConnectFn = websockets_connect,
        *,
        on_disconnect: Callable[[ErrorCode], None] | None = None,
    ) -> None:
        raise NotImplementedError

    async def connect_and_register(self, client_key: str | None) -> str:
        """Connect, perform SSAP register handshake, and start the reader loop."""
        raise NotImplementedError

    async def request(self, uri: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a one-shot SSAP request and await its matched response payload."""
        raise NotImplementedError

    async def subscribe(
        self,
        uri: str,
        callback: Callable[[dict[str, Any]], Awaitable[None] | None],
    ) -> None:
        """Subscribe to an SSAP endpoint and route push updates to callback."""
        raise NotImplementedError

    async def close(self) -> None:
        """Close the SSAP transport and cancel the background reader loop."""
        raise NotImplementedError
