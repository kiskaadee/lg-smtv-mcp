"""Core LG webOS TV transport, protocol, and state machine package."""

from lg_smtv_mcp.tv_core.manager import ConnectionState, TvManager, TvState, send_wol
from lg_smtv_mcp.tv_core.pointer import BUTTON_ALLOWLIST, PointerClient, normalize_button_keys
from lg_smtv_mcp.tv_core.protocols import (
    CaptureResult,
    CaptureTransport,
    FrameCapturer,
    KeysResult,
    TextResult,
    TvStateSnapshot,
    TvStatus,
    WebSocketClosedError,
    WebSocketConnectFn,
    WebSocketTransport,
)
from lg_smtv_mcp.tv_core.ssap import (
    PERMISSIONS,
    SsapClient,
    build_register_payload,
    websockets_connect,
)

__all__ = [
    "BUTTON_ALLOWLIST",
    "CaptureResult",
    "CaptureTransport",
    "ConnectionState",
    "FrameCapturer",
    "KeysResult",
    "PERMISSIONS",
    "PointerClient",
    "SsapClient",
    "TextResult",
    "TvManager",
    "TvState",
    "TvStateSnapshot",
    "TvStatus",
    "WebSocketClosedError",
    "WebSocketConnectFn",
    "WebSocketTransport",
    "build_register_payload",
    "normalize_button_keys",
    "send_wol",
    "websockets_connect",
]
