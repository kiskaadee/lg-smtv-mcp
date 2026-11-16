"""CLI entrypoint for lg-smtv-mcp (serve and pair subcommands)."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from lg_smtv_mcp.auth import ASGIApp
from lg_smtv_mcp.config import TvConfig, load_config
from lg_smtv_mcp.tv_core.protocols import WebSocketConnectFn
from lg_smtv_mcp.tv_core.ssap import websockets_connect


def _default_uvicorn_run(app: ASGIApp, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


def _default_stdio_run(server: MCPServer[Any]) -> None:
    server.run(transport="stdio")


def _default_fetch_url(url: str) -> bytes:
    from urllib.request import urlopen

    with urlopen(url, timeout=10.0) as resp:  # noqa: S310
        return bytes(resp.read())


def main(
    argv: Sequence[str] | None = None,
    *,
    config_loader: Callable[[], TvConfig] = load_config,
    uvicorn_run: Callable[[ASGIApp, str, int], None] = _default_uvicorn_run,
    stdio_run: Callable[[MCPServer[Any]], None] = _default_stdio_run,
    connect_fn: WebSocketConnectFn = websockets_connect,
    fetch_url: Callable[[str], bytes] = _default_fetch_url,
    env_path: Path | None = None,
) -> int:
    """CLI entrypoint for `lg-smtv-mcp serve` and `lg-smtv-mcp pair`."""
    _ = (argv, config_loader, uvicorn_run, stdio_run, connect_fn, fetch_url, env_path)
    raise NotImplementedError
