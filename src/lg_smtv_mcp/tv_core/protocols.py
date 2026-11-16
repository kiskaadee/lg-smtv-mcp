"""Transport protocols, connection exceptions, and frozen result containers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol


class WebSocketClosedError(Exception):
    """Raised when a WebSocket transport closes unexpectedly or is already closed."""

    def __init__(self, code: int, reason: str = "") -> None:
        message = f"WebSocket closed ({code}): {reason}" if reason else f"WebSocket closed ({code})"
        super().__init__(message)
        self.code = code
        self.reason = reason


class WebSocketTransport(Protocol):
    """Minimal async WebSocket transport boundary for SSAP and pointer sockets."""

    async def send(self, data: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


type WebSocketConnectFn = Callable[[str], Awaitable[WebSocketTransport]]


class CaptureTransport(Protocol):
    """Minimal async SFTP transport boundary for Developer Mode screenshot retrieval."""

    async def read_and_remove(
        self,
        remote_path: str,
        timeout: float = 6.0,
        cleanup_grace: float = 1.5,
    ) -> bytes: ...

    async def sweep_temp_files(self, prefix: str = "/tmp/lg_smtv_cap_") -> None: ...

    async def close(self) -> None: ...


class FrameCapturer(Protocol):
    """Protocol for capturing screenshots via SSAP + SFTP."""

    async def capture_frame(
        self,
        ssap_request_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> CaptureResult: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TvStatus:
    """Result of a tv_wake operation."""

    status: Literal["connected", "already_connected"]
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class TvStateSnapshot:
    """Point-in-time read-only snapshot of cached TV state."""

    connected: bool
    stale: bool
    volume: int | None
    muted: bool | None
    sound_output: str | None
    foreground_app: str | None
    last_updated: float | None


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Result of a screenshot capture operation."""

    image_bytes: bytes
    frame_sha256: str
    changed_since_last: bool
    size_bytes: int


@dataclass(frozen=True, slots=True)
class KeysResult:
    """Result of a batched pointer key sequence."""

    keys_sent: bool
    count: int
    frame_sha256: str | None = None
    changed_since_last: bool | None = None
    size_bytes: int | None = None
    capture_error: str | None = None


@dataclass(frozen=True, slots=True)
class TextResult:
    """Result of an IME text-entry operation."""

    text_sent: bool
    enter_sent: bool
    frame_sha256: str | None = None
    changed_since_last: bool | None = None
    size_bytes: int | None = None
    capture_error: str | None = None
