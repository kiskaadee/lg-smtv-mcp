# lg-smtv-mcp

Model Context Protocol (MCP) server for local network control of LG Smart TVs (webOS) over SSAP WebSockets, Wake-on-LAN, and optional Developer Mode SSH screenshot capture.

## Setup

```bash
nix develop
```

Provisions Python 3.14, `websockets`, `ruff`, and `pyright` via [flake.nix](flake.nix).

## Historical PoC & Empirical Notes

Initial feasibility probes and verified device findings (`65UR8750PSA` / `WebOS/4.1.0`) are archived in [`doc/poc/`](doc/poc/):

- [doc/poc/capabilities.md](doc/poc/capabilities.md) — Verified SSAP/DIAL endpoints, deep-link formats, `401` boundaries, and Developer Mode SSH screenshot pipeline.
- [doc/poc/discover.py](doc/poc/discover.py) — SSDP multicast probe (`--all` to list DIAL/DLNA/SSAP services and MAC address).
- [doc/poc/tv_test.py](doc/poc/tv_test.py) — One-shot SSAP pairing + command runner.
