"""MCP server, ASGI lifespan management, and LG webOS TV tool definitions."""

import asyncio
import base64
import json
import signal
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent

from lg_smtv_mcp.auth import ASGIApp, build_transport_security_settings, wrap_bearer_auth
from lg_smtv_mcp.config import TvConfig, require_bearer_token
from lg_smtv_mcp.errors import ConfigError, TvError
from lg_smtv_mcp.tv_core import AsyncSshCaptureTransport, CaptureClient, TvManager


async def _run_tv_call[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        return await fn()
    except TvError as exc:
        raise ToolError(f"{exc.code.value}: {exc.message}") from exc
    except ConfigError as exc:
        raise ToolError(exc.message) from exc


def _build_capture_content(
    metadata: dict[str, Any],
    image_bytes: bytes | None,
) -> list[TextContent | ImageContent]:
    items: list[TextContent | ImageContent] = [
        TextContent(type="text", text=json.dumps(metadata)),
    ]
    if image_bytes is not None:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        items.append(ImageContent(type="image", data=encoded, mime_type="image/jpeg"))
    return items


def _register_tools(server: MCPServer, tv_manager: TvManager, config: TvConfig) -> None:
    @server.tool(description="Wake the TV via Wake-on-LAN and wait until SSAP is connected.")
    async def tv_wake() -> dict[str, Any]:
        status = await _run_tv_call(tv_manager.wake)
        return asdict(status)

    @server.tool(description="Return cached TV state without blocking on standby or UI locks.")
    async def get_state() -> dict[str, Any]:
        return asdict(tv_manager.get_state())

    @server.tool(description="Send a batch of remote control button keys with optional screenshot.")
    async def keys(
        keys: list[str],
        delay_ms: int = 250,
        settle_ms: int = 800,
        capture: bool = False,
    ) -> list[TextContent | ImageContent]:
        res = await _run_tv_call(
            lambda: tv_manager.send_keys(
                keys, delay_ms=delay_ms, settle_ms=settle_ms, capture=capture
            )
        )
        metadata: dict[str, Any] = {"keys_sent": res.keys_sent, "count": res.count}
        if res.frame_sha256 is not None:
            metadata["frame_sha256"] = res.frame_sha256
        if res.changed_since_last is not None:
            metadata["changed_since_last"] = res.changed_since_last
        if res.size_bytes is not None:
            metadata["size_bytes"] = res.size_bytes
        if res.capture_error is not None:
            metadata["capture_error"] = res.capture_error
        return _build_capture_content(metadata, res.image_bytes)

    @server.tool(
        description=(
            "Insert text via webOS IME (requires the TV's native on-screen keyboard to be open), "
            "optionally pressing Enter and capturing a screenshot."
        )
    )
    async def type_text(
        text: str,
        send_enter: bool = True,
        capture: bool = False,
    ) -> list[TextContent | ImageContent]:
        res = await _run_tv_call(
            lambda: tv_manager.type_text(text, send_enter=send_enter, capture=capture)
        )
        metadata: dict[str, Any] = {
            "text_sent": res.text_sent,
            "enter_sent": res.enter_sent,
        }
        if res.frame_sha256 is not None:
            metadata["frame_sha256"] = res.frame_sha256
        if res.changed_since_last is not None:
            metadata["changed_since_last"] = res.changed_since_last
        if res.size_bytes is not None:
            metadata["size_bytes"] = res.size_bytes
        if res.capture_error is not None:
            metadata["capture_error"] = res.capture_error
        return _build_capture_content(metadata, res.image_bytes)

    @server.tool(description="Capture a TV screenshot and return JPEG image content plus metadata.")
    async def screenshot() -> list[TextContent | ImageContent]:
        res = await _run_tv_call(tv_manager.screenshot)
        metadata: dict[str, Any] = {
            "frame_sha256": res.frame_sha256,
            "changed_since_last": res.changed_since_last,
            "size_bytes": res.size_bytes,
        }
        return _build_capture_content(metadata, res.image_bytes)

    @server.tool(description="Launch a webOS app by ID with optional contentTarget and params.")
    async def launch(
        app_id: str,
        content_target: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await _run_tv_call(
            lambda: tv_manager.launch_app(app_id, content_target=content_target, params=params)
        )

    @server.tool(description="List installed TV apps as [{id, title}].")
    async def list_apps() -> list[dict[str, str]]:
        return await _run_tv_call(tv_manager.list_apps)

    @server.tool(description="Set TV volume within [0, MAX_VOLUME].")
    async def set_volume(level: int) -> dict[str, Any]:
        return await _run_tv_call(lambda: tv_manager.set_volume(level))

    @server.tool(description="Set TV audio mute state.")
    async def set_mute(muted: bool) -> dict[str, Any]:
        return await _run_tv_call(lambda: tv_manager.set_mute(muted))

    @server.tool(description="List external TV inputs as [{id, label, connected}].")
    async def list_inputs() -> list[dict[str, Any]]:
        return await _run_tv_call(tv_manager.list_inputs)

    @server.tool(description="Switch active external TV input by input_id.")
    async def switch_input(input_id: str) -> dict[str, Any]:
        return await _run_tv_call(lambda: tv_manager.switch_input(input_id))

    @server.tool(description="Power off the TV into standby.")
    async def turn_off() -> dict[str, Any]:
        return await _run_tv_call(tv_manager.turn_off)

    if config.enable_raw_ssap:

        @server.tool(
            description="Send a raw SSAP request (enabled only when ENABLE_RAW_SSAP=true)."
        )
        async def raw_ssap(uri: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            return await _run_tv_call(lambda: tv_manager.raw_ssap(uri, payload))


def create_mcp_server(config: TvConfig, manager: TvManager | None = None) -> MCPServer:
    """Create and configure the MCPServer instance with TV tools and lifespan."""
    tv_manager = (
        manager
        if manager is not None
        else TvManager(config, capture_client=CaptureClient(AsyncSshCaptureTransport(config)))
    )

    @asynccontextmanager
    async def _lifespan(_server: MCPServer[None]) -> AsyncGenerator[None]:
        loop = asyncio.get_running_loop()
        await tv_manager.start()
        sighup_registered = False
        if hasattr(signal, "SIGHUP"):
            with suppress(NotImplementedError, AttributeError, ValueError):
                loop.add_signal_handler(signal.SIGHUP, tv_manager.reload_client_key)
                sighup_registered = True
        try:
            yield
        finally:
            if sighup_registered and hasattr(signal, "SIGHUP"):
                with suppress(NotImplementedError, AttributeError, ValueError):
                    loop.remove_signal_handler(signal.SIGHUP)
            await tv_manager.close()

    server = MCPServer(name="lg-smtv-mcp", lifespan=_lifespan)
    _register_tools(server, tv_manager, config)
    return server


def create_http_app(config: TvConfig, manager: TvManager | None = None) -> ASGIApp:
    """Create the Bearer-authenticated Streamable HTTP ASGI application."""
    token = require_bearer_token(config)
    server = create_mcp_server(config, manager=manager)
    app = server.streamable_http_app(
        transport_security=build_transport_security_settings(config),
        host=config.host,
    )
    return wrap_bearer_auth(app, token)
