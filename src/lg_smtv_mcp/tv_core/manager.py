"""Single-owner TV connection state machine, subscribe cache, and control surface."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.tv_core.protocols import (
    CaptureResult,
    FrameCapturer,
    KeysResult,
    TextResult,
    TvStateSnapshot,
    TvStatus,
    WebSocketConnectFn,
)
from lg_smtv_mcp.tv_core.ssap import websockets_connect


class ConnectionState(StrEnum):
    """Lifecycle states of the single-owner TvManager connection loop."""

    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    STANDBY_OFFLINE = "STANDBY_OFFLINE"
    WAKING = "WAKING"
    PAIRING_REQUIRED = "PAIRING_REQUIRED"


@dataclass(slots=True)
class TvState:
    """Mutable internal cache of TV state populated by SSAP subscribe streams."""

    volume: int | None = None
    muted: bool | None = None
    sound_output: str | None = None
    foreground_app: str | None = None
    last_updated: float | None = None


def send_wol(mac: str, broadcast_addr: str = "255.255.255.255", port: int = 9) -> None:
    """Broadcast a Wake-on-LAN magic packet for the given MAC address."""
    raise NotImplementedError


class TvManager:
    """Single-owner connection state machine and serialized control surface."""

    state: ConnectionState

    def __init__(
        self,
        config: TvConfig,
        *,
        connect_fn: WebSocketConnectFn = websockets_connect,
        capture_client: FrameCapturer | None = None,
        wol_fn: Callable[[str, str, int], None] = send_wol,
        backoff_steps: tuple[float, ...] = (2.0, 5.0, 15.0, 30.0),
        wake_poll_interval: float = 1.0,
        wake_timeout: float = 45.0,
        ui_lock_timeout: float = 25.0,
    ) -> None:
        raise NotImplementedError

    async def start(self) -> None:
        """Start the background connection state machine loop."""
        raise NotImplementedError

    async def close(self) -> None:
        """Stop the connection loop and close all underlying transports."""
        raise NotImplementedError

    def reload_client_key(self) -> None:
        """Reload the saved client key from disk (e.g. on SIGHUP)."""
        raise NotImplementedError

    async def wake(self) -> TvStatus:
        """Send a Wake-on-LAN packet and wait for the TV to register."""
        raise NotImplementedError

    def get_state(self) -> TvStateSnapshot:
        """Return a lock-free snapshot of the cached TV state."""
        raise NotImplementedError

    async def send_keys(
        self,
        keys: list[str],
        delay_ms: int = 250,
        settle_ms: int = 800,
        capture: bool = False,
    ) -> KeysResult:
        """Validate, serialize, and send pointer button keys with optional capture."""
        raise NotImplementedError

    async def type_text(
        self,
        text: str,
        send_enter: bool = True,
        capture: bool = False,
    ) -> TextResult:
        """Send IME text input with optional enter key and screenshot capture."""
        raise NotImplementedError

    async def screenshot(self) -> CaptureResult:
        """Acquire ui_lock and capture a screenshot via Developer Mode SFTP."""
        raise NotImplementedError

    async def launch_app(
        self,
        app_id: str,
        content_target: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Launch an installed webOS app with optional deep-link parameters."""
        raise NotImplementedError

    async def list_apps(self) -> list[dict[str, str]]:
        """Return installed launch points as [{id, title}]."""
        raise NotImplementedError

    async def set_volume(self, level: int) -> dict[str, Any]:
        """Set TV volume within [0, config.max_volume]."""
        raise NotImplementedError

    async def set_mute(self, muted: bool) -> dict[str, Any]:
        """Set TV mute state."""
        raise NotImplementedError

    async def list_inputs(self) -> list[dict[str, Any]]:
        """Return external inputs as [{id, label, connected}]."""
        raise NotImplementedError

    async def switch_input(self, input_id: str) -> dict[str, Any]:
        """Switch active external input."""
        raise NotImplementedError

    async def turn_off(self) -> dict[str, Any]:
        """Power off the TV into standby."""
        raise NotImplementedError

    async def raw_ssap(self, uri: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Forward a raw SSAP request when enabled by configuration."""
        raise NotImplementedError
