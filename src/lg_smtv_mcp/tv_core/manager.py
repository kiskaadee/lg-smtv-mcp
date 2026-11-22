"""Single-owner TV connection state machine, subscribe cache, and control surface."""

import asyncio
import json
import logging
import socket
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from lg_smtv_mcp.config import TvConfig, require_mac
from lg_smtv_mcp.errors import ConfigError, ErrorCode, TvError
from lg_smtv_mcp.tv_core.pointer import PointerClient, normalize_button_keys
from lg_smtv_mcp.tv_core.protocols import (
    CaptureResult,
    FrameCapturer,
    KeysResult,
    TextResult,
    TvStateSnapshot,
    TvStatus,
    WebSocketConnectFn,
)
from lg_smtv_mcp.tv_core.ssap import SsapClient, websockets_connect

logger = logging.getLogger(__name__)

type EventWaitFn = Callable[[asyncio.Event, float], Awaitable[None]]


async def wait_event_or_timeout(event: asyncio.Event, timeout: float) -> None:
    """Wait until event is set or timeout seconds elapse, whichever comes first."""
    with suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), timeout=timeout)


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
    clean_hex = mac.replace(":", "").replace("-", "").strip()
    if len(clean_hex) != 12:
        raise ConfigError(f"Invalid MAC address for Wake-on-LAN: {mac!r}")
    try:
        mac_bytes = bytes.fromhex(clean_hex)
    except ValueError as exc:
        raise ConfigError(f"Invalid MAC address for Wake-on-LAN: {mac!r}") from exc

    magic = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, (broadcast_addr, port))


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
        wait_fn: EventWaitFn = wait_event_or_timeout,
    ) -> None:
        self._config = config
        self._connect_fn = connect_fn
        self._capture_client = capture_client
        self._wol_fn = wol_fn
        self._wait_fn = wait_fn
        self._backoff_steps = backoff_steps
        self._wake_poll_interval = wake_poll_interval
        self._wake_timeout = wake_timeout
        self._ui_lock_timeout = ui_lock_timeout

        self.state = ConnectionState.CONNECTING
        self._tv_state = TvState()
        self._ui_lock = asyncio.Lock()
        self._client_key: str | None = None

        self._ssap: SsapClient | None = None
        self._pointer: PointerClient | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._wake_task: asyncio.Task[TvStatus] | None = None
        self._stopping = False

        self._wake_or_reload_event = asyncio.Event()
        self._disconnected_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._wake_done_event = asyncio.Event()
        self._disconnect_code = ErrorCode.TV_OFFLINE_STANDBY
        self._wake_requested = False
        self._reload_requested = False
        self._wake_failure: TvError | None = None

    async def start(self) -> None:
        """Start the background connection state machine loop."""
        if self._loop_task is None or self._loop_task.done():
            self._stopping = False
            self._loop_task = asyncio.create_task(self._connection_loop())

    async def close(self) -> None:
        """Stop the connection loop and close all underlying transports."""
        self._stopping = True
        self._wake_or_reload_event.set()
        self._disconnected_event.set()

        if self._wake_task is not None and not self._wake_task.done():
            self._wake_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._wake_task

        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._loop_task
        self._loop_task = None

        await self._teardown_transports(close_capture=True)

    def reload_client_key(self) -> None:
        """Reload the saved client key from disk (e.g. on SIGHUP)."""
        loaded = self._try_load_client_key()
        if loaded is not None:
            self._client_key = loaded
            self._reload_requested = True
            if self.state is ConnectionState.PAIRING_REQUIRED:
                self._wake_or_reload_event.set()

    async def wake(self) -> TvStatus:
        """Send a Wake-on-LAN packet and wait for the TV to register."""
        if self.state is ConnectionState.CONNECTED:
            return TvStatus(status="already_connected", elapsed_ms=0)
        if self.state is ConnectionState.PAIRING_REQUIRED:
            raise TvError(
                ErrorCode.PAIRING_REQUIRED,
                "TV requires pairing before wake; run pair CLI first",
            )

        if self._wake_task is None or self._wake_task.done():
            self._wake_task = asyncio.create_task(self._run_wake())
        return await asyncio.shield(self._wake_task)

    def get_state(self) -> TvStateSnapshot:
        """Return a lock-free snapshot of the cached TV state."""
        connected = self.state is ConnectionState.CONNECTED
        return TvStateSnapshot(
            connected=connected,
            stale=not connected,
            volume=self._tv_state.volume,
            muted=self._tv_state.muted,
            sound_output=self._tv_state.sound_output,
            foreground_app=self._tv_state.foreground_app,
            last_updated=self._tv_state.last_updated,
        )

    async def _run_wake(self) -> TvStatus:
        mac = require_mac(self._config)
        start = time.monotonic()
        self._wol_fn(mac, self._config.wol_broadcast, 9)

        self._wake_failure = None
        self._connected_event.clear()
        self._wake_done_event.clear()
        self._wake_requested = True
        self._wake_or_reload_event.set()

        if self.state is ConnectionState.CONNECTED:
            self._wake_requested = False
            return TvStatus(status="already_connected", elapsed_ms=0)

        wait_connected = asyncio.create_task(self._connected_event.wait())
        wait_failed = asyncio.create_task(self._wake_done_event.wait())
        try:
            async with asyncio.timeout(self._wake_timeout):
                done, _ = await asyncio.wait(
                    {wait_connected, wait_failed},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wait_failed in done and self._wake_failure is not None:
                    raise self._wake_failure
        except TimeoutError as exc:
            if self.state is ConnectionState.WAKING:
                self.state = ConnectionState.STANDBY_OFFLINE
            raise TvError(
                ErrorCode.WAKE_TIMEOUT,
                f"TV did not become ready within {self._wake_timeout:.0f}s",
            ) from exc
        finally:
            self._wake_requested = False
            for task in (wait_connected, wait_failed):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return TvStatus(status="connected", elapsed_ms=elapsed_ms)

    async def _connection_loop(self) -> None:
        backoff_idx = 0
        while not self._stopping:
            if self._wake_requested:
                self.state = ConnectionState.WAKING
            elif self.state not in (ConnectionState.CONNECTING, ConnectionState.WAKING):
                self.state = ConnectionState.CONNECTING

            try:
                await self._connect_and_subscribe()
            except TvError as exc:
                await self._teardown_transports(
                    close_capture=exc.code is not ErrorCode.PAIRING_REQUIRED
                )
                if exc.code is ErrorCode.PAIRING_REQUIRED:
                    self.state = ConnectionState.PAIRING_REQUIRED
                    self._wake_requested = False
                    self._wake_failure = exc
                    self._wake_done_event.set()
                    backoff_idx = await self._wait_backoff(backoff_idx)
                    continue

                if self._wake_requested:
                    self.state = ConnectionState.WAKING
                    await asyncio.sleep(self._wake_poll_interval)
                    continue

                self.state = ConnectionState.STANDBY_OFFLINE
                backoff_idx = await self._wait_backoff(backoff_idx)
                continue

            backoff_idx = 0
            self.state = ConnectionState.CONNECTED
            self._disconnected_event.clear()
            self._connected_event.set()

            await self._disconnected_event.wait()
            if self._stopping:
                break

            is_pairing = self._disconnect_code is ErrorCode.PAIRING_REQUIRED
            await self._teardown_transports(close_capture=not is_pairing)
            if is_pairing:
                self._wake_requested = False
            self.state = (
                ConnectionState.PAIRING_REQUIRED if is_pairing else ConnectionState.STANDBY_OFFLINE
            )
            backoff_idx = await self._wait_backoff(backoff_idx)

    async def _wait_backoff(self, backoff_idx: int) -> int:
        current_idx = backoff_idx
        while not self._stopping:
            if self._wake_requested:
                return 0
            if self.state is ConnectionState.PAIRING_REQUIRED and self._reload_requested:
                self._reload_requested = False
                return current_idx

            delay = self._backoff_steps[min(current_idx, len(self._backoff_steps) - 1)]
            next_idx = min(current_idx + 1, len(self._backoff_steps) - 1)
            self._wake_or_reload_event.clear()

            # Re-check after clearing the event so concurrent wake/reload signals are not lost.
            if self._wake_requested:
                return 0
            if self.state is ConnectionState.PAIRING_REQUIRED and self._reload_requested:
                self._reload_requested = False
                return current_idx

            await self._wait_fn(self._wake_or_reload_event, delay)

            if self.state is ConnectionState.PAIRING_REQUIRED and not self._wake_requested:
                self._reload_requested = False
                reloaded = self._try_load_client_key()
                if reloaded is not None:
                    self._client_key = reloaded
                    return next_idx
                current_idx = next_idx
                continue
            return next_idx
        return current_idx

    async def _connect_and_subscribe(self) -> None:
        if self._client_key is None:
            self._client_key = self._try_load_client_key()

        ssap = SsapClient(
            f"wss://{self._config.tv_ip}:3001",
            connect_fn=self._connect_fn,
            on_disconnect=self._on_ssap_disconnect,
        )
        self._ssap = ssap
        resolved_key = await ssap.connect_and_register(self._client_key)
        if resolved_key:
            self._client_key = resolved_key

        await ssap.subscribe("ssap://audio/getStatus", self._handle_audio_status)
        await ssap.subscribe(
            "ssap://com.webos.applicationManager/getForegroundAppInfo",
            self._handle_foreground_app,
        )
        self._pointer = PointerClient(ssap.request, connect_fn=self._connect_fn)

    async def _teardown_transports(self, *, close_capture: bool) -> None:
        pointer = self._pointer
        self._pointer = None
        if pointer is not None:
            await pointer.close()

        ssap = self._ssap
        self._ssap = None
        if ssap is not None:
            await ssap.close()

        if close_capture and self._capture_client is not None:
            with suppress(Exception):
                await self._capture_client.close()

    def _on_ssap_disconnect(self, code: ErrorCode) -> None:
        self._disconnect_code = code
        self._disconnected_event.set()

    def _try_load_client_key(self) -> str | None:
        key_path = self._config.client_key_path
        if not key_path.is_file():
            return None
        try:
            raw: Any = json.loads(key_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read client key file %s: %s", key_path, exc)
            return None
        if not isinstance(raw, dict):
            logger.warning("Client key file %s does not contain a JSON object", key_path)
            return None
        mapping = cast(dict[str, Any], raw)
        val = mapping.get(self._config.tv_ip)
        if isinstance(val, str) and val.strip():
            return val.strip()
        return None

    def _handle_audio_status(self, payload: dict[str, Any]) -> None:
        volume = payload.get("volume")
        if isinstance(volume, int) and not isinstance(volume, bool):
            self._tv_state.volume = volume

        mute = payload.get("mute")
        if isinstance(mute, bool):
            self._tv_state.muted = mute

        volume_status = payload.get("volumeStatus")
        if isinstance(volume_status, dict):
            vs_dict = cast(dict[str, Any], volume_status)
            sound_output = vs_dict.get("soundOutput")
            if isinstance(sound_output, str):
                self._tv_state.sound_output = sound_output
        elif isinstance(payload.get("soundOutput"), str):
            self._tv_state.sound_output = str(payload["soundOutput"])

        self._tv_state.last_updated = time.time()

    def _handle_foreground_app(self, payload: dict[str, Any]) -> None:
        app_id = payload.get("appId")
        if isinstance(app_id, str):
            self._tv_state.foreground_app = app_id
        self._tv_state.last_updated = time.time()

    def _require_ssap(self) -> SsapClient:
        if self.state is ConnectionState.PAIRING_REQUIRED:
            raise TvError(
                ErrorCode.PAIRING_REQUIRED,
                "TV requires pairing; run pair CLI and reload client key",
            )
        if self.state is not ConnectionState.CONNECTED or self._ssap is None:
            raise TvError(ErrorCode.TV_OFFLINE_STANDBY, "TV is offline or in standby")
        return self._ssap

    def _require_pointer(self) -> PointerClient:
        self._require_ssap()
        if self._pointer is None:
            raise TvError(ErrorCode.TV_OFFLINE_STANDBY, "Pointer client is not connected")
        return self._pointer

    @asynccontextmanager
    async def _acquire_ui_lock(self) -> AsyncGenerator[None]:
        try:
            await asyncio.wait_for(self._ui_lock.acquire(), timeout=self._ui_lock_timeout)
        except TimeoutError as exc:
            raise TvError(
                ErrorCode.LOCK_TIMEOUT,
                f"Timed out waiting {self._ui_lock_timeout:.0f}s for UI lock",
            ) from exc
        try:
            yield
        finally:
            self._ui_lock.release()

    async def _capture_optional(self, ssap: SsapClient) -> tuple[CaptureResult | None, str | None]:
        if self._capture_client is None:
            return None, "SCREENSHOT_UNAVAILABLE: Developer Mode SSH is not configured"
        try:
            return await self._capture_client.capture_frame(ssap.request), None
        except TvError as exc:
            if exc.code is ErrorCode.SCREENSHOT_UNAVAILABLE:
                return None, str(exc)
            raise

    async def send_keys(
        self,
        keys: list[str],
        delay_ms: int = 250,
        settle_ms: int = 800,
        capture: bool = False,
    ) -> KeysResult:
        """Validate, serialize, and send pointer button keys with optional capture."""
        if not (1 <= len(keys) <= 20):
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                f"keys must contain between 1 and 20 items, got {len(keys)}",
            )
        if not (0 <= delay_ms <= 500):
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                f"delay_ms must be between 0 and 500, got {delay_ms}",
            )
        if not (0 <= settle_ms <= 2000):
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                f"settle_ms must be between 0 and 2000, got {settle_ms}",
            )
        normalize_button_keys(keys)

        ssap = self._require_ssap()
        pointer = self._require_pointer()

        async with self._acquire_ui_lock():
            count = await pointer.send_buttons(keys, delay_ms=delay_ms)
            if not capture:
                return KeysResult(keys_sent=True, count=count)
            if settle_ms > 0:
                await asyncio.sleep(settle_ms / 1000.0)
            cap, cap_err = await self._capture_optional(ssap)
            return KeysResult(
                keys_sent=True,
                count=count,
                frame_sha256=cap.frame_sha256 if cap is not None else None,
                changed_since_last=cap.changed_since_last if cap is not None else None,
                size_bytes=cap.size_bytes if cap is not None else None,
                capture_error=cap_err,
                image_bytes=cap.image_bytes if cap is not None else None,
            )

    async def type_text(
        self,
        text: str,
        send_enter: bool = True,
        capture: bool = False,
    ) -> TextResult:
        """Send IME text input with optional enter key and screenshot capture."""
        if not (1 <= len(text) <= 500):
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                f"text length must be between 1 and 500 characters, got {len(text)}",
            )
        ssap = self._require_ssap()
        async with self._acquire_ui_lock():
            await ssap.request(
                "ssap://com.webos.service.ime/insertText",
                {"text": text, "replace": 0},
            )
            if send_enter:
                await ssap.request("ssap://com.webos.service.ime/sendEnterKey", {})
            if not capture:
                return TextResult(text_sent=True, enter_sent=send_enter)
            cap, cap_err = await self._capture_optional(ssap)
            return TextResult(
                text_sent=True,
                enter_sent=send_enter,
                frame_sha256=cap.frame_sha256 if cap is not None else None,
                changed_since_last=cap.changed_since_last if cap is not None else None,
                size_bytes=cap.size_bytes if cap is not None else None,
                capture_error=cap_err,
                image_bytes=cap.image_bytes if cap is not None else None,
            )

    async def screenshot(self) -> CaptureResult:
        """Acquire ui_lock and capture a screenshot via Developer Mode SFTP."""
        ssap = self._require_ssap()
        async with self._acquire_ui_lock():
            if self._capture_client is None:
                raise TvError(
                    ErrorCode.SCREENSHOT_UNAVAILABLE,
                    "Developer Mode SSH is not configured",
                )
            return await self._capture_client.capture_frame(ssap.request)

    async def launch_app(
        self,
        app_id: str,
        content_target: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Launch an installed webOS app with optional deep-link parameters."""
        ssap = self._require_ssap()
        merged_params = dict(params or {})
        if content_target is not None:
            merged_params["contentTarget"] = content_target
        payload: dict[str, Any] = {"id": app_id}
        if merged_params:
            payload["params"] = merged_params
        return await ssap.request("ssap://system.launcher/launch", payload)

    async def list_apps(self) -> list[dict[str, str]]:
        """Return installed launch points as [{id, title}]."""
        ssap = self._require_ssap()
        resp = await ssap.request("ssap://com.webos.applicationManager/listLaunchPoints", {})
        raw_points = resp.get("launchPoints", [])
        if not isinstance(raw_points, list):
            return []
        apps: list[dict[str, str]] = []
        for item_raw in cast(list[Any], raw_points):
            if isinstance(item_raw, dict):
                item = cast(dict[str, Any], item_raw)
                apps.append({"id": str(item.get("id", "")), "title": str(item.get("title", ""))})
        return apps

    async def set_volume(self, level: int) -> dict[str, Any]:
        """Set TV volume within [0, config.max_volume]."""
        if level < 0 or level > self._config.max_volume:
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                f"Volume {level} must be between 0 and {self._config.max_volume}",
            )
        ssap = self._require_ssap()
        return await ssap.request("ssap://audio/setVolume", {"volume": level})

    async def set_mute(self, muted: bool) -> dict[str, Any]:
        """Set TV mute state."""
        ssap = self._require_ssap()
        return await ssap.request("ssap://audio/setMute", {"mute": muted})

    async def list_inputs(self) -> list[dict[str, Any]]:
        """Return external inputs as [{id, label, connected}]."""
        ssap = self._require_ssap()
        resp = await ssap.request("ssap://tv/getExternalInputList", {})
        raw_devices = resp.get("devices", [])
        if not isinstance(raw_devices, list):
            return []
        inputs: list[dict[str, Any]] = []
        for item_raw in cast(list[Any], raw_devices):
            if isinstance(item_raw, dict):
                item = cast(dict[str, Any], item_raw)
                inputs.append(
                    {
                        "id": str(item.get("id", "")),
                        "label": str(item.get("label", "")),
                        "connected": bool(item.get("connected", False)),
                    }
                )
        return inputs

    async def switch_input(self, input_id: str) -> dict[str, Any]:
        """Switch active external input."""
        ssap = self._require_ssap()
        return await ssap.request("ssap://tv/switchInput", {"inputId": input_id})

    async def turn_off(self) -> dict[str, Any]:
        """Power off the TV into standby."""
        ssap = self._require_ssap()
        response = await ssap.request("ssap://system/turnOff", {})
        if response.get("returnValue") is True:
            self.state = ConnectionState.STANDBY_OFFLINE
            self._connected_event.clear()
            await self._teardown_transports(close_capture=True)
            self._on_ssap_disconnect(ErrorCode.TV_OFFLINE_STANDBY)
        return response

    async def raw_ssap(self, uri: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Forward a raw SSAP request when enabled by configuration."""
        if not self._config.enable_raw_ssap:
            raise TvError(
                ErrorCode.INVALID_ARGUMENT,
                "raw_ssap is disabled (ENABLE_RAW_SSAP=false)",
            )
        ssap = self._require_ssap()
        return await ssap.request(uri, payload)
