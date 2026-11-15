"""Minimal LG webOS SSAP pairing and command test script."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import ssl
import time
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
    "CONTROL_INPUT_JOYSTICK",
    "CONTROL_INPUT_TV",
    "CONTROL_TV_MEDIA",
    "CONTROL_MOUSE_AND_KEYBOARD",
    "READ_INSTALLED_APPS",
    "READ_RUNNING_APPS",
    "READ_INPUT_DEVICE_LIST",
    "READ_TV_CHANNEL_LIST",
    "READ_POWER_STATE",
    "WRITE_NOTIFICATION_TOAST",
]

COMMANDS: dict[str, tuple[str, dict[str, Any]]] = {
    "info": ("ssap://system/getSystemInfo", {}),
    "apps": ("ssap://com.webos.applicationManager/listLaunchPoints", {}),
    "foreground": ("ssap://com.webos.applicationManager/getForegroundAppInfo", {}),
    "inputs": ("ssap://tv/getExternalInputList", {}),
    "audio": ("ssap://audio/getStatus", {}),
    "vol-up": ("ssap://audio/volumeUp", {}),
    "vol-down": ("ssap://audio/volumeDown", {}),
    "mute": ("ssap://audio/setMute", {"mute": True}),
    "unmute": ("ssap://audio/setMute", {"mute": False}),
    "turn-off": ("ssap://system/turnOff", {}),
}


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    """Broadcast a Wake-on-LAN magic packet for the given MAC address."""
    clean_hex = mac.replace(":", "").replace("-", "")
    if len(clean_hex) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    magic = b"\xff" * 6 + bytes.fromhex(clean_hex) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, (broadcast, port))


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


def build_register_payload(client_key: str | None, force_pairing: bool = False) -> dict[str, Any]:
    """Build the SSAP register handshake payload."""
    payload: dict[str, Any] = {
        "forcePairing": force_pairing,
        "pairingType": "PROMPT",
        "manifest": {
            "manifestVersion": 1,
            "appVersion": "0.1.0",
            "permissions": PERMISSIONS,
            "localizedAppNames": {"": "webOS Linux Test"},
        },
    }
    if client_key and not force_pairing:
        payload["client-key"] = client_key
    return {"type": "register", "id": "register_0", "payload": payload}


async def pair_and_send(
    ip: str,
    port: int,
    uri: str,
    payload: dict[str, Any],
    key_file: Path,
    re_pair: bool = False,
) -> dict[str, Any]:
    """Connect to the TV, complete registration/pairing, and send one SSAP request."""
    keys = load_client_keys(key_file)
    saved_key = None if re_pair else keys.get(ip)
    scheme = "wss" if port == 3001 else "ws"
    ssl_ctx = build_ssl_context() if scheme == "wss" else None

    async with websockets.connect(f"{scheme}://{ip}:{port}", ssl=ssl_ctx) as ws:
        await ws.send(json.dumps(build_register_payload(saved_key, force_pairing=re_pair)))

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


async def wake_and_wait(
    ip: str,
    port: int,
    mac: str,
    key_file: Path,
    broadcast: str = "255.255.255.255",
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    """Send WoL magic packet and poll SSAP until registration succeeds."""
    send_wol(mac, broadcast=broadcast)
    start = time.monotonic()
    last_error = ""
    attempts = 0
    keys = load_client_keys(key_file)
    saved_key = keys.get(ip)
    scheme = "wss" if port == 3001 else "ws"
    ssl_ctx = build_ssl_context() if scheme == "wss" else None

    while (time.monotonic() - start) < timeout_s:
        attempts += 1
        try:
            async with websockets.connect(
                f"{scheme}://{ip}:{port}", ssl=ssl_ctx, open_timeout=4.0
            ) as ws:
                await ws.send(json.dumps(build_register_payload(saved_key)))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                if msg.get("type") == "registered":
                    return {
                        "status": "connected",
                        "attempts": attempts,
                        "elapsed_ms": round((time.monotonic() - start) * 1000),
                        "last_error": last_error,
                    }
                last_error = f"unexpected frame: {msg.get('type')}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(1.0)

    raise TimeoutError(f"Wake timed out after {attempts} attempts ({last_error})")


async def verify_subscriptions(ip: str, port: int, key_file: Path) -> dict[str, Any]:
    """Verify SSAP subscribe frames, push updates on state change, and id multiplexing."""
    keys = load_client_keys(key_file)
    saved_key = keys.get(ip)
    scheme = "wss" if port == 3001 else "ws"
    ssl_ctx = build_ssl_context() if scheme == "wss" else None

    async with websockets.connect(
        f"{scheme}://{ip}:{port}", ssl=ssl_ctx, ping_interval=10, ping_timeout=5
    ) as ws:
        await ws.send(json.dumps(build_register_payload(saved_key)))
        reg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
        if reg.get("type") != "registered":
            raise RuntimeError(f"Expected registered, got: {reg.get('type')}")

        await ws.send(
            json.dumps({"id": "sub_audio", "type": "subscribe", "uri": "ssap://audio/getStatus"})
        )
        await ws.send(
            json.dumps(
                {
                    "id": "sub_fg",
                    "type": "subscribe",
                    "uri": "ssap://com.webos.applicationManager/getForegroundAppInfo",
                }
            )
        )
        await ws.send(
            json.dumps(
                {"id": "req_info", "type": "request", "uri": "ssap://system/getSystemInfo"}
            )
        )

        frames: list[dict[str, Any]] = []
        for _ in range(3):
            frames.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0)))

        # Trigger a mute toggle to verify sub_audio pushes a second frame automatically
        await ws.send(
            json.dumps(
                {
                    "id": "req_mute_on",
                    "type": "request",
                    "uri": "ssap://audio/setMute",
                    "payload": {"mute": True},
                }
            )
        )
        for _ in range(2):
            frames.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0)))

        await ws.send(
            json.dumps(
                {
                    "id": "req_mute_off",
                    "type": "request",
                    "uri": "ssap://audio/setMute",
                    "payload": {"mute": False},
                }
            )
        )
        for _ in range(2):
            frames.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0)))

        return {"frames": frames}


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
    parser.add_argument(
        "--re-pair",
        action="store_true",
        help="Force a new pairing prompt on the TV (e.g. after expanding permissions).",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    toast_p = sub.add_parser("toast", help="Show a toast notification on the TV.")
    toast_p.add_argument("message", nargs="?", default="Hello from Linux!")

    launch_p = sub.add_parser("launch", help="Launch an app by ID (e.g. youtube.leanback.v4).")
    launch_p.add_argument("app_id", help="App ID from `apps` output.")
    launch_p.add_argument("--params", default="{}", help="Optional JSON params for the app.")

    for name in COMMANDS:
        sub.add_parser(name, help=f"Send {COMMANDS[name][0]}")

    raw_p = sub.add_parser("raw", help="Send an arbitrary SSAP URI + JSON payload.")
    raw_p.add_argument("uri", help="SSAP URI (e.g. ssap://system/getSystemInfo)")
    raw_p.add_argument("--payload", default="{}", help="JSON payload string")

    wake_p = sub.add_parser("wake", help="Wake TV via WoL and poll until SSAP registers.")
    wake_p.add_argument("--mac", required=True, help="TV MAC address (XX:XX:XX:XX:XX:XX)")
    wake_p.add_argument("--broadcast", default="255.255.255.255", help="WoL broadcast IP")

    sub.add_parser("subscribe-test", help="Verify SSAP subscriptions and id multiplexing.")
    return parser.parse_args()


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    if args.command == "wake":
        response = asyncio.run(
            wake_and_wait(args.ip, args.port, args.mac, args.key_file, broadcast=args.broadcast)
        )
        print(json.dumps(response, indent=2))
        return
    if args.command == "subscribe-test":
        response = asyncio.run(verify_subscriptions(args.ip, args.port, args.key_file))
        print(json.dumps(response, indent=2))
        return

    if args.command == "toast":
        uri = "ssap://system.notifications/createToast"
        payload: dict[str, Any] = {"message": args.message}
    elif args.command == "launch":
        uri = "ssap://system.launcher/launch"
        payload = {"id": args.app_id, "params": json.loads(args.params)}
    elif args.command == "raw":
        uri = args.uri
        payload = json.loads(args.payload)
    else:
        uri, payload = COMMANDS[args.command]

    response = asyncio.run(
        pair_and_send(args.ip, args.port, uri, payload, args.key_file, re_pair=args.re_pair)
    )
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
