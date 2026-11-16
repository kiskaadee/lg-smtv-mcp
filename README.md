# lg-smtv-mcp

A Python Model Context Protocol (MCP) server for persistent, local-network control of LG webOS Smart TVs.

`lg-smtv-mcp` provides an agent-friendly control plane over LG's Simple Service Access Protocol (SSAP), combining persistent WebSocket connections, remote pointer button input, subscription-based device state tracking, Wake-on-LAN recovery, and optional Developer Mode screenshot capture.

The server is deliberately **LLM-free**: it exposes bounded, deterministic operations so external AI agents (such as Antigravity, Claude, or a vision-guided decision loop) can reason over UI state and control the television without embedding model APIs into the daemon.

> **Status: Active development (Milestone M1).**
> Configuration validation, error taxonomy, and the [`tv_core`](src/lg_smtv_mcp/tv_core/) engine (SSAP multiplexer, `PointerClient`, and `TvManager` state machine) are implemented and unit-tested offline. FastMCP tool bindings, Bearer authentication, CLI entrypoints (`pair` / `serve`), and end-to-end verification are currently in progress; Developer Mode SFTP screenshot capture is planned for M2.

---

## Why a Persistent Control Plane?

Controlling a webOS TV through one-shot scripts introduces avoidable latency and reliability issues: every command has to re-open a TLS WebSocket, re-run the SSAP `register` handshake, re-acquire a pointer input socket, and (for vision workflows) open a fresh SSH/SFTP session.

`lg-smtv-mcp` addresses this by separating two responsibilities:

- **MCP Host (Client / Agent):** Owns reasoning, planning, and vision-guided UI navigation.
- **MCP Server (`lg-smtv-mcp`):** Owns persistent device connections, protocol multiplexing, cached device state, and bounded command execution.

Keeping those boundaries separate keeps the server deterministic, fast, and independent of any specific LLM provider or orchestration framework.

---

## Architecture Overview

The codebase centers on [`tv_core`](src/lg_smtv_mcp/tv_core/), which owns the TV's communication channels and connection state machine:

| Component | Status | Responsibility |
| :--- | :--- | :--- |
| [`config.py`](src/lg_smtv_mcp/config.py) & [`errors.py`](src/lg_smtv_mcp/errors.py) | Implemented | Pydantic v2 `TvConfig` validation, `.env` loader, decoupled `ConfigError` vs. `TvError` taxonomy |
| [`SsapClient`](src/lg_smtv_mcp/tv_core/ssap.py) | Implemented | Persistent TLS SSAP WebSocket, `msg["id"]` request/subscribe multiplexing, `1008` standby mapping |
| [`PointerClient`](src/lg_smtv_mcp/tv_core/pointer.py) | Implemented | Ephemeral webOS pointer socket for allowlisted remote button frames and mid-batch reconnect |
| [`TvManager`](src/lg_smtv_mcp/tv_core/manager.py) | Implemented | Single-owner connection loop, stepped backoff, `wake()` coalescing, `subscribe` cache, `ui_lock`, control surface |
| MCP Server & CLI (`server.py`, `cli.py`) | In progress (M1) | FastMCP tools, `streamable-http` Bearer auth, `pair` / `serve` CLI, `SIGHUP` key reload |
| Developer Mode Capture (`capture.py`) | Planned (M2) | `asyncssh` SFTP screenshot retrieval behind the [`FrameCapturer`](src/lg_smtv_mcp/tv_core/protocols.py) protocol |

### Communication Channels

- **SSAP (`wss://<TV_IP>:3001`):** JSON-over-WebSocket for device control, app launching, IME text input, external input switching, and `subscribe` state streams (`audio/getStatus`, `getForegroundAppInfo`).
- **Pointer Input Socket (`wss://...`):** Line-oriented socket obtained via SSAP for remote D-pad and navigation button events (`UP`, `DOWN`, `ENTER`, `HOME`, `BACK`, etc.).
- **Wake-on-LAN (UDP):** Explicit device wake-up via magic packets, separated from normal control requests so standby detection fails fast instead of silently waking the TV.
- **Developer Mode SSH/SFTP (Planned M2):** Optional remote screenshot capture (`FrameCapturer` interface already wired in `TvManager` with graceful partial-success degradation when unavailable).

### Design Principles

- **Persistent connections:** Keep SSAP WebSocket, pointer socket, and subscription state warm across tool calls.
- **Explicit state & recovery:** Track `CONNECTING`, `CONNECTED`, `STANDBY_OFFLINE`, `WAKING`, and `PAIRING_REQUIRED` explicitly; mutating calls fail fast on standby (`1008`), while read-only state queries return cached values marked `stale: true`.
- **Deterministic & bounded operations:** Enforce allowlisted button keys, batch size/delay limits, `MAX_VOLUME` guardrails, and opt-in `ENABLE_RAW_SSAP`.
- **Graceful degradation:** Missing or expired Developer Mode SSH returns `capture_error` alongside `keys_sent: true` / `text_sent: true` without breaking SSAP control.
- **LLM independence:** Zero model SDKs, API keys, or internal agent loops in the server.

---

## Current Capabilities & Roadmap

Implementation is tracked on the [Project Pipeline](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/projects/6):

| Milestone | Scope | Status |
| :--- | :--- | :--- |
| **M1** | Core config, `tv_core` engine, FastMCP tools, Bearer auth, `pair`/`serve` CLI, E2E verification | In progress (`#1` & `#2` merged; `#3` in progress; `#4` queued) |
| **M2** | Developer Mode SFTP screenshot capture (`asyncssh`), systemd unit, extended surface | Planned (`#5`) |

Implemented in [`TvManager`](src/lg_smtv_mcp/tv_core/manager.py) today (not yet exposed over MCP until `#3` completes):
- Persistent SSAP multiplexing and `subscribe`-backed state snapshots (`volume`, `muted`, `sound_output`, `foreground_app`)
- Stepped reconnect backoff and explicit `wake()` coalescing (`WAKE_TIMEOUT`)
- Allowlisted pointer button sequences (`send_keys`) and IME text entry (`type_text`)
- App discovery/launching (`list_apps`, `launch_app`), volume/mute guardrails (`set_volume`, `set_mute`), input discovery/switching (`list_inputs`, `switch_input`), `turn_off`, and gated `raw_ssap`

---

## Requirements & Tested Hardware

- **Runtime:** Python 3.14+ (or Nix with Flakes enabled)
- **Hardware:** An LG webOS Smart TV reachable on the local network (with Wake-on-LAN and LG Connect Apps enabled for power-on and SSAP control)
- **Optional (M2):** LG Developer Mode enabled for SSH/SFTP screenshot capture

Initial protocol exploration and empirical verification were performed against:
- **Device:** `65UR8750PSA`
- **Platform:** `WebOS/4.1.0`

Compatibility with other LG webOS models and firmware versions has not yet been established.

---

## Development Setup

This repository uses Nix Flakes ([flake.nix](flake.nix)) for reproducible environments:

```bash
git clone https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp.git
cd lg-smtv-mcp
nix develop
```

If you use `direnv`, run `direnv allow` to activate [`.envrc`](.envrc) automatically.

The shell provisions Python 3.14, `websockets`, `mcp`, `pydantic`, `asyncssh`, `uvicorn`, `httpx`, `pytest`, `pytest-asyncio`, `ruff`, and `pyright`, and configures `.githooks/` as the Git hooks path.

---

## Configuration

Runtime configuration is loaded by [`load_config()`](src/lg_smtv_mcp/config.py) from process environment variables with `.env` fallback (existing process environment variables always take precedence). See [`.env.example`](.env.example):

```bash
cp .env.example .env
```

| Variable | Purpose | Default / Requirement |
| :--- | :--- | :--- |
| `TV_IP` | Target TV IPv4 address | Required |
| `TV_MAC` | Target TV MAC address for Wake-on-LAN | Required for `wake` |
| `WOL_BROADCAST` | Wake-on-LAN UDP broadcast IPv4 address | `255.255.255.255` |
| `CLIENT_KEY_PATH` | SSAP pairing client-key JSON path | `.client_key.json` |
| `SSH_KEY_PATH` | Optional Developer Mode SSH private key path | Unset |
| `WEBOS_SSH_PASSPHRASE` | Optional SSH key passphrase | Unset |
| `SSH_KNOWN_HOSTS_PATH` | Optional SSH `known_hosts` path | Unset |
| `HOST` | HTTP server bind address | `127.0.0.1` |
| `PORT` | HTTP server port (`1..65535`) | `8765` |
| `ALLOWED_HOSTS` | Comma-separated permitted HTTP `Host` headers | Empty |
| `MCP_BEARER_TOKEN` | Bearer token for `streamable-http` transport | Required for HTTP |
| `MAX_VOLUME` | Upper bound guardrail (`0..100`) for `set_volume` | `40` |
| `ENABLE_RAW_SSAP` | Enables optional `raw_ssap` pass-through (`true`/`false`) | `false` |

> **Note:** `TvConfig` and `load_config()` are implemented, while the MCP server runtime and `pair` / `serve` CLI are still under active development in M1.

---

## Verification

All automated tests run offline against mocked transport boundaries (`FakeWebSocketTransport` / `FrameCapturer`) and never contact physical hardware.

Inside `nix develop`:

```bash
ruff check .
ruff format --check .
pyright
pytest
```

Or run the full clean-room Nix sandbox verification (matching Gitea Actions CI in [`.gitea/workflows/ci.yaml`](.gitea/workflows/ci.yaml)):

```bash
nix flake check
```

---

## Documentation

- [Architecture & Design RFC](docs/designs/lg-smtv-mcp.md) — Component contracts, state machine, error taxonomy, and implementation plan
- [Verified Capabilities](docs/poc/capabilities.md) — Empirical SSAP, DIAL, deep-link, and Developer Mode findings
- [Crunchyroll E2E Experiment](docs/poc/crunchyroll-e2e-experiment.md) — Initial vision-guided navigation experiment and latency measurements
- [Agent & Engineering Guidelines](AGENTS.md) — Coding standards, validation boundaries, protocol invariants, and CI rules

---

## License

Released into the public domain under the [Unlicense](LICENSE).
