"""Shared offline test fakes and fixtures for lg-smtv-mcp contract tests."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from lg_smtv_mcp.tv_core.protocols import WebSocketClosedError


class FakeWebSocketTransport:
    """In-memory WebSocketTransport double for offline SSAP and pointer tests."""

    def __init__(
        self,
        *,
        on_send: Callable[[str, FakeWebSocketTransport], None] | None = None,
    ) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._on_send = on_send
        self._incoming: asyncio.Queue[str | Exception] = asyncio.Queue()

    def push_text(self, data: str) -> None:
        """Enqueue a raw text frame to be returned by recv()."""
        self._incoming.put_nowait(data)

    def push_json(self, payload: dict[str, Any]) -> None:
        """Enqueue a JSON-serialized frame to be returned by recv()."""
        self._incoming.put_nowait(json.dumps(payload))

    def push_error(self, exc: Exception) -> None:
        """Enqueue an exception to be raised by the next recv()."""
        self._incoming.put_nowait(exc)

    def push_close(self, code: int = 1000, reason: str = "") -> None:
        """Enqueue a WebSocketClosedError to simulate remote socket close."""
        self._incoming.put_nowait(WebSocketClosedError(code, reason))

    async def send(self, data: str) -> None:
        """Record an outgoing frame or raise WebSocketClosedError if closed."""
        if self.closed:
            raise WebSocketClosedError(1006, "transport closed")
        self.sent.append(data)
        if self._on_send is not None:
            self._on_send(data, self)

    async def recv(self) -> str:
        """Return the next queued frame or raise a queued exception."""
        if self.closed and self._incoming.empty():
            raise WebSocketClosedError(1006, "transport closed")
        item = await self._incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        """Mark the transport closed and wake any blocked recv()."""
        if not self.closed:
            self.closed = True
            self._incoming.put_nowait(WebSocketClosedError(1000, "closed"))
