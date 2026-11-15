"""SSDP multicast discovery probe for LG webOS TVs on the local network."""

from __future__ import annotations

import argparse
import socket

LG_WEBOS_ST = "urn:lge-com:service:webos-second-screen:1"
SSDP_ADDR = ("239.255.255.250", 1900)


def discover(search_target: str, timeout: float = 3.0) -> list[tuple[str, str]]:
    """Send an SSDP M-SEARCH multicast packet and collect responses."""
    payload = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {search_target}\r\n"
        "\r\n"
    ).encode()

    results: list[tuple[str, str]] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(payload, SSDP_ADDR)
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                break
            results.append((addr[0], data.decode(errors="replace")))
    return results


def main() -> None:
    """CLI entry point for SSDP discovery."""
    parser = argparse.ArgumentParser(description="Discover LG webOS TVs via SSDP.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Search for ssdp:all instead of only LG webOS devices.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Seconds to wait for responses (default: 3.0).",
    )
    args = parser.parse_args()

    target = "ssdp:all" if args.all else LG_WEBOS_ST
    print(f"Sending SSDP M-SEARCH for {target} (timeout={args.timeout}s)...")
    responses = discover(target, timeout=args.timeout)
    if not responses:
        print("No SSDP responses received.")
        return

    for ip, raw in responses:
        print(f"\n=== {ip} ===")
        print(raw.strip())


if __name__ == "__main__":
    main()
