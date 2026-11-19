"""CLI entrypoint for lg-smtv-mcp (serve and pair subcommands)."""

import argparse
import asyncio
import ipaddress
import json
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from mcp.server.mcpserver import MCPServer
from websockets.exceptions import WebSocketException

from lg_smtv_mcp.auth import ASGIApp
from lg_smtv_mcp.config import TvConfig, load_config
from lg_smtv_mcp.errors import ConfigError, ErrorCode, TvError
from lg_smtv_mcp.server import create_http_app, create_mcp_server
from lg_smtv_mcp.tv_core.protocols import WebSocketConnectFn
from lg_smtv_mcp.tv_core.ssap import build_register_payload, websockets_connect


def _default_uvicorn_run(app: ASGIApp, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


def _default_stdio_run(server: MCPServer[Any]) -> None:
    server.run(transport="stdio")


def _default_fetch_url(url: str) -> bytes:
    from urllib.request import urlopen

    with urlopen(url, timeout=10.0) as resp:  # noqa: S310
        return bytes(resp.read())


def _atomic_write_0600(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        os.chmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def _load_existing_keys(key_path: Path) -> dict[str, str]:
    if not key_path.is_file():
        return {}
    try:
        raw = json.loads(key_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    raw_dict = cast(dict[Any, Any], raw)
    return {str(k): str(v) for k, v in raw_dict.items() if isinstance(v, str) and v}


async def _pair_tv(
    ip: str,
    existing_key: str | None,
    connect_fn: WebSocketConnectFn,
) -> str:
    ws = await connect_fn(f"wss://{ip}:3001")
    try:
        reg_payload = build_register_payload(existing_key)
        await ws.send(json.dumps(reg_payload))
        while True:
            raw = await ws.recv()
            frame: dict[str, Any] = json.loads(raw)
            msg_type = frame.get("type")
            if msg_type == "registered":
                payload = frame.get("payload")
                if isinstance(payload, dict):
                    payload_dict = cast(dict[str, Any], payload)
                    client_key = payload_dict.get("client-key")
                    if isinstance(client_key, str) and client_key:
                        return client_key
                raise TvError(ErrorCode.PAIRING_REQUIRED, "TV registration returned no client-key")
            if msg_type == "error":
                raise TvError(
                    ErrorCode.PAIRING_REQUIRED,
                    f"TV pairing rejected: {frame.get('error', 'unknown error')}",
                )
    finally:
        await ws.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lg-smtv-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the MCP server.")
    serve_parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio"],
        default="streamable-http",
        help="MCP transport to serve.",
    )

    pair_parser = subparsers.add_parser("pair", help="Pair with an LG webOS TV.")
    pair_parser.add_argument("--ip", default=None, help="TV IPv4 address (defaults to TV_IP).")
    pair_parser.add_argument(
        "--fetch-ssh-key",
        action="store_true",
        help="Also fetch Developer Mode SSH private key from http://<TV_IP>:9991/webos_rsa.",
    )
    pair_parser.add_argument(
        "--ssh-key-path",
        type=Path,
        default=None,
        help="Destination path for fetched Developer Mode SSH key.",
    )
    return parser


def _handle_serve(
    args: argparse.Namespace,
    config: TvConfig,
    *,
    uvicorn_run: Callable[[ASGIApp, str, int], None],
    stdio_run: Callable[[MCPServer[Any]], None],
) -> int:
    if args.transport == "stdio":
        server = create_mcp_server(config)
        stdio_run(server)
        return 0

    app = create_http_app(config)
    uvicorn_run(app, config.host, config.port)
    return 0


def _handle_pair(
    args: argparse.Namespace,
    config: TvConfig,
    *,
    connect_fn: WebSocketConnectFn,
    fetch_url: Callable[[str], bytes],
) -> int:
    raw_ip: str = args.ip if args.ip is not None else config.tv_ip
    try:
        tv_ip = str(ipaddress.IPv4Address(raw_ip.strip()))
    except ValueError as exc:
        raise ConfigError(f"Invalid TV IPv4 address: {raw_ip!r}") from exc

    existing_keys = _load_existing_keys(config.client_key_path)
    client_key = asyncio.run(_pair_tv(tv_ip, existing_keys.get(tv_ip), connect_fn))
    existing_keys[tv_ip] = client_key
    _atomic_write_0600(
        config.client_key_path,
        (json.dumps(existing_keys, indent=2) + "\n").encode("utf-8"),
    )

    if args.fetch_ssh_key:
        ssh_dest: Path = args.ssh_key_path or config.ssh_key_path or Path(".webos_rsa")
        key_bytes = fetch_url(f"http://{tv_ip}:9991/webos_rsa")
        if not key_bytes.strip():
            raise TvError(
                ErrorCode.SCREENSHOT_UNAVAILABLE,
                "Fetched Developer Mode SSH key was empty",
            )
        _atomic_write_0600(ssh_dest, key_bytes)

    return 0


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
    _ = env_path
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        config = config_loader()
        if args.command == "serve":
            return _handle_serve(args, config, uvicorn_run=uvicorn_run, stdio_run=stdio_run)
        return _handle_pair(args, config, connect_fn=connect_fn, fetch_url=fetch_url)
    except (ConfigError, TvError, WebSocketException, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
