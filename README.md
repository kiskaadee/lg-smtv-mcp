# lg-smtv-mcp

Model Context Protocol (MCP) server for local network control of LG Smart TVs (webOS) over SSAP WebSockets, Wake-on-LAN, and optional Developer Mode SSH screenshot capture.

## Setup

```bash
nix develop
```

Provisions Python 3.14, `websockets`, `ruff`, and `pyright` via [flake.nix](flake.nix).

## Historical PoC & Empirical Notes

Initial feasibility probes and verified device findings (`65UR8750PSA` / `WebOS/4.1.0`) are archived in [`docs/poc/`](docs/poc/):

- [docs/poc/capabilities.md](docs/poc/capabilities.md) — Verified SSAP/DIAL endpoints, deep-link formats, `401` boundaries, and Developer Mode SSH screenshot pipeline.
- [docs/poc/crunchyroll-e2e-experiment.md](docs/poc/crunchyroll-e2e-experiment.md) — Timed end-to-end vision-loop experiment (Crunchyroll → *Attack on Titan* Ep 1) and latency findings.
- [docs/poc/discover.py](docs/poc/discover.py) — SSDP multicast probe (`--all` to list DIAL/DLNA/SSAP services and MAC address).
- [docs/poc/tv_test.py](docs/poc/tv_test.py) — One-shot SSAP pairing + command runner.

## Design & RFC

- [docs/designs/lg-smtv-mcp.md](docs/designs/lg-smtv-mcp.md) — Architecture RFC for the `lg-smtv-mcp` server.
