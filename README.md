# webos-testing

Testing LG webOS local network control (SSDP discovery, SSAP WebSocket RPC, DIAL) from Linux/Python.

## Setup

```bash
nix develop
```

Provisions Python 3.14, `websockets`, `ruff`, and `pyright` via [flake.nix](flake.nix).

## Scripts

- `discover.py` — SSDP multicast probe (`--all` to list DIAL/DLNA/SSAP services and MAC address).
- `tv_test.py` — One-shot SSAP pairing + command runner (`toast`, `info`, `apps`, `foreground`, `inputs`, `audio`, `launch`, `vol-up`, `vol-down`, `mute`, `raw`).

## Documentation

- [docs/capabilities.md](docs/capabilities.md) — Verified SSAP/DIAL capabilities, deep-link contracts, and boundaries observed on `65UR8750PSA` (`WebOS/4.1.0`).
