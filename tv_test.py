"""Minimal LG webOS SSAP pairing and command test script."""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
from pathlib import Path
from typing import Any

import websockets

DEFAULT_KEY_FILE = Path(".client_key.json")

PERMISSIONS = [
    "LAUNCH",
    "CONTROL_AUDIO",
    "CONTROL_DISPLAY",
    "CONTROL_POWER",
    "CONTROL_INPUT_TEXT",
    "CONTROL_INPUT_MEDIA_PLAYBACK",
    "READ_INSTALLED_APPS",
    "WRITE_NOTIFICATION_TOAST",
]

COMMANDS: dict[str, tuple[str, dict[str, Any]]] = {
    "info": ("ssap://system/getSystemInfo", {}),
    "apps": ("ssap://com.webos.applicationManager/listLaunchPoints", {}),
    "vol-up": ("ssap://audio/volumeUp", {}),
    "vol-down": ("ssap://audio/volumeDown", {}),
    "mute": ("ssap://audio/setMute", {"mute": True}),
    "unmute": ("ssap://audio/setMute", {"mute": False}),
}


def build_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that accepts the TV's self-signed certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def load_client_keys(key_file: Path) -> dict[str, str]:
    """Load saved IP -> client-key mappings from disk."""
    if not key_file.exists():
        return {}
    data = json.loads(key_file.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def save_client_key(key_file: Path, ip: str, client_key: str) -> None:
    """Persist a paired TV's client-key to disk."""
    keys = load_client_keys(key_file)
    keys[ip] = client_key
    key_file.write_text(json.dumps(keys, indent=2) + "\n", encoding="utf-8")


def build_register_payload(client_key: str | None) -> dict[str, Any]:
    """Build the SSAP register handshake payload."""
    payload: dict[str, Any] = {
        "forcePairing": False,
        "pairingType": "PROMPT",
        "manifest": {
            "manifestVersion": 1,
            "appVersion": "0.1.0",
            "permissions": PERMISSIONS,
            "localizedAppNames": {"": "webOS Linux Test"},
        },
    }
    if client_key:
        payload["client-key"] = client_key
    return {"type": "register", "id": "register_0", "payload": payload}


async def pair_and_send(
    ip: str,
    port: int,
    uri: str,
    payload: dict[str, Any],
    key_file: Path,
) -> dict[str, Any]:
    """Connect to the TV, complete registration/pairing, and send one SSAP request."""
    keys = load_client_keys(key_file)
    saved_key = keys.get(ip)
    scheme = "wss" if port == 3001 else "ws"
    ssl_ctx = build_ssl_context() if scheme == "wss" else None

    async with websockets.connect(f"{scheme}://{ip}:{port}", ssl=ssl_ctx) as ws:
        await ws.send(json.dumps(build_register_payload(saved_key)))

        while True:
            msg = json.loads(await ws.recv())
            msg_type = msg.get("type")
            if msg_type == "response" and msg.get("payload", {}).get("pairingType") == "PROMPT":
                print("Pairing prompt sent — accept it on the TV screen...")
                continue
            if msg_type == "registered":
                new_key = msg["payload"]["client-key"]
                if new_key != saved_key:
                    save_client_key(key_file, ip, new_key)
                    print(f"Paired! Saved client-key to {key_file}")
                break
            if msg_type == "error":
                raise RuntimeError(f"Registration failed: {msg}")

        req = {"id": "cmd_1", "type": "request", "uri": uri, "payload": payload}
        await ws.send(json.dumps(req))
        return json.loads(await ws.recv())


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Pair with an LG webOS TV and send a test command."
    )
    parser.add_argument("--ip", required=True, help="TV IP address (e.g. 192.168.1.11)")
    parser.add_argument("--port", type=int, default=3001, help="WebSocket port (default: 3001)")
    parser.add_argument(
        "--key-file",
        type=Path,
        default=DEFAULT_KEY_FILE,
        help="Path to store client-key (default: .client_key.json)",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    toast_p = sub.add_parser("toast", help="Show a toast notification on the TV.")
    toast_p.add_argument("message", nargs="?", default="Hello from Linux!")

    for name in COMMANDS:
        sub.add_parser(name, help=f"Send {COMMANDS[name][0]}")

    raw_p = sub.add_parser("raw", help="Send an arbitrary SSAP URI + JSON payload.")
    raw_p.add_argument("uri", help="SSAP URI (e.g. ssap://system/getSystemInfo)")
    raw_p.add_argument("--payload", default="{}", help="JSON payload string")
    return parser.parse_args()


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    if args.command == "toast":
        uri = "ssap://system.notifications/createToast"
        payload: dict[str, Any] = {"message": args.message}
    elif args.command == "raw":
        uri = args.uri
        payload = json.loads(args.payload)
    else:
        uri, payload = COMMANDS[args.command]

    response = asyncio.run(pair_and_send(args.ip, args.port, uri, payload, args.key_file))
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
