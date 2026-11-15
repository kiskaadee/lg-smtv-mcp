"""Typed domain error taxonomy for lg-smtv-mcp."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Canonical error codes mapped to MCP tool errors."""

    TV_OFFLINE_STANDBY = "TV_OFFLINE_STANDBY"
    PAIRING_REQUIRED = "PAIRING_REQUIRED"
    SCREENSHOT_UNAVAILABLE = "SCREENSHOT_UNAVAILABLE"
    LOCK_TIMEOUT = "LOCK_TIMEOUT"
    WAKE_TIMEOUT = "WAKE_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"


class TvError(Exception):
    """Domain error carrying a structured ErrorCode and message."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
