# lg-smtv-mcp

[![M8ven Score](https://m8ven.ai/badge/mcp/kiskaadee-lg-smtv-mcp-1hkv9o?v=e22cd6151bbeb9e9a4b51cacc3bc775e)](https://m8ven.ai/mcp/kiskaadee-lg-smtv-mcp-1hkv9o?s=readme)

A Python Model Context Protocol (MCP) server for persistent, local-network control of LG webOS Smart TVs.

`lg-smtv-mcp` provides an agent-friendly control plane over LG's Simple Service Access Protocol (SSAP), combining persistent WebSocket connections, remote pointer button input, subscription-based device state tracking, Wake-on-LAN recovery, and optional Developer Mode screenshot capture.

The server is deliberately **LLM-free**: it exposes bounded, deterministic operations so external AI agents (such as Antigravity, Claude, or a vision-guided decision loop) can reason over UI state and control the television without embedding model APIs into the daemon.

> **Status: M2 delivered — M2.1 live-TV polish in progress.**
> Configuration validation, error taxonomy, [`tv_core`](src/lg_smtv_mcp/tv_core/) (SSAP multiplexer, `PointerClient`, `TvManager`), FastMCP server & Bearer auth ([`server.py`](src/lg_smtv_mcp/server.py), [`auth.py`](src/lg_smtv_mcp/auth.py)), `pair` / `serve` CLI ([`cli.py`](src/lg_smtv_mcp/cli.py)), full-stack offline E2E verification ([`test_e2e_mvp.py`](tests/test_e2e_mvp.py)), Developer Mode SFTP screenshot capture with optional host-side JPEG downscaling ([`capture.py`](src/lg_smtv_mcp/tv_core/capture.py)), and `systemd` unit sandboxing hardening ([#17](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/issues/17)) are implemented on `main`. Ongoing M2.1 polish is tracked in [ROADMAP.md](ROADMAP.md).

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
| [`server.py`](src/lg_smtv_mcp/server.py), [`auth.py`](src/lg_smtv_mcp/auth.py), [`cli.py`](src/lg_smtv_mcp/cli.py) | Implemented | FastMCP tools, `streamable-http` Bearer auth & host validation, `pair` / `serve` CLI, `SIGHUP` key reload |
| Developer Mode Capture ([`capture.py`](src/lg_smtv_mcp/tv_core/capture.py)) | Implemented | `CaptureClient` + `AsyncSshCaptureTransport`: `asyncssh` SFTP screenshot retrieval behind the [`FrameCapturer`](src/lg_smtv_mcp/tv_core/protocols.py) / `CaptureTransport` protocols, with a shared `6s` deadline and `/tmp` sweep |

### Communication Channels

- **SSAP (`wss://<TV_IP>:3001`):** JSON-over-WebSocket for device control, app launching, IME text input, external input switching, and `subscribe` state streams (`audio/getStatus`, `getForegroundAppInfo`).
- **Pointer Input Socket (`wss://...`):** Line-oriented socket obtained via SSAP for remote D-pad and navigation button events (`UP`, `DOWN`, `ENTER`, `HOME`, `BACK`, etc.).
- **Wake-on-LAN (UDP):** Explicit device wake-up via magic packets, separated from normal control requests so standby detection fails fast instead of silently waking the TV.
- **Developer Mode SSH/SFTP (`prisoner@<TV_IP>:9922`):** Optional remote screenshot capture over a reusable `asyncssh` session (`CaptureClient` behind the `FrameCapturer` protocol, default-wired in [`server.py`](src/lg_smtv_mcp/server.py)). When Developer Mode SSH is unavailable, `keys`/`type_text` still return partial success with `capture_error`, and `screenshot` fails with `SCREENSHOT_UNAVAILABLE`.

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
| **M1** | Core config, `tv_core` engine, FastMCP tools, Bearer auth, `pair`/`serve` CLI, E2E verification | Complete (`#1`–`#4` merged on `main`) |
| **M2** | Developer Mode SFTP screenshot capture (`asyncssh`), systemd unit, E2E test hardening, sandboxing | Complete (`#11`–`#13`, `#17` merged on `main`) |
| **M2.1** | Host-side JPEG downscaling (`#20`), immediate `turn_off` standby (`#21`), Nix sandbox tests (`#22`), settle retry (`#23`) | In progress (`#20` merged; `#21`–`#23` open) |

Implemented in [`TvManager`](src/lg_smtv_mcp/tv_core/manager.py) and exposed via [`server.py`](src/lg_smtv_mcp/server.py):
- Persistent SSAP multiplexing and `subscribe`-backed state snapshots (`get_state`: `volume`, `muted`, `sound_output`, `foreground_app`)
- Stepped reconnect backoff and explicit Wake-on-LAN coalescing (`tv_wake`, bounded by `WAKE_TIMEOUT`)
- Allowlisted pointer button sequences (`keys`) and IME text entry (`type_text`) with optional `capture` hook (`screenshot`)
- App discovery/launching (`list_apps`, `launch`), volume/mute guardrails (`set_volume`, `set_mute`), input discovery/switching (`list_inputs`, `switch_input`), `turn_off`, and opt-in `raw_ssap` (`ENABLE_RAW_SSAP=true`)

---

## Prerequisites & Tested Hardware

Before running the server, make sure you have:

- **Host Runtime:** Nix with Flakes enabled (recommended) or Python `3.14+`
- **Network:** Host and LG webOS Smart TV on the **same local network** (Wake-on-LAN uses UDP broadcast; SSAP uses `wss://<TV_IP>:3001`)
- **LG webOS TV Settings:**
  - **LG Connect Apps** enabled (*Settings -> General -> Devices -> External Devices -> LG Connect Apps*, or *Network -> LG Connect Apps* depending on webOS version)
  - **Wake-on-LAN / Mobile TV On** enabled (*Turn On via Wi-Fi / Network*) if you want `tv_wake` to power on the TV from standby
  - **Static IP / DHCP reservation** (recommended) and the TV's **MAC address**
- **Optional (for Screenshots):** An LG Developer account and the **Developer Mode** app installed on the TV with **Dev Mode Status** and **Key Server** enabled (SSH/SFTP on port `9922`)

Initial protocol exploration and live-TV verification were performed against:
- **Device:** `65UR8750PSA`
- **Platform:** `WebOS/4.1.0`

Compatibility with other LG webOS models and firmware versions has not yet been established.

---

## Getting Started (Step-by-Step)

### Step 1: Prepare the Host Environment

Clone the repository and enter the Nix devShell ([`flake.nix`](flake.nix)):

```bash
git clone https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp.git
cd lg-smtv-mcp
nix develop
```

If you use `direnv`, run `direnv allow` to activate [`.envrc`](.envrc) automatically.

The shell provisions Python 3.14, `websockets`, `mcp`, `pydantic`, `asyncssh`, `uvicorn`, `pillow`, `httpx`, `pytest`, `pytest-asyncio`, `ruff`, and `pyright`, and configures `.githooks/` as the Git hooks path.

> **Note:** Inside `nix develop`, run commands using `python -m lg_smtv_mcp <subcommand>` (or `nix run . -- <subcommand>`), as the devShell provisions the Python environment directly rather than installing package console scripts to `PATH`.

### Step 2: Create and Configure `.env`

Copy [`.env.example`](.env.example) to `.env` and fill in your TV's IP, MAC address, and a freshly generated Bearer token (never commit `.env`, `.client_key.json`, or SSH keys):

```bash
cp .env.example .env
openssl rand -hex 32   # use output as MCP_BEARER_TOKEN
```

Minimal `.env` for local use:

```dotenv
TV_IP=<TV_IP>
TV_MAC=<TV_MAC>
MCP_BEARER_TOKEN=<GENERATED_TOKEN>
HOST=127.0.0.1
PORT=8765
MAX_VOLUME=40
```

If you enable Developer Mode screenshots, also consider setting `CAPTURE_MAX_WIDTH=480` and `CAPTURE_JPEG_QUALITY=55` (as in [`.env.example`](.env.example)) so screenshots stay compact enough for inline MCP delivery without disk offloading.

### Step 3: Pair with the TV (One-Time)

Power **on** the TV and ensure *LG Connect Apps* is enabled. Then run:

```bash
python -m lg_smtv_mcp pair
# Or override TV_IP:
python -m lg_smtv_mcp pair --ip <TV_IP>
```

If you also want **Developer Mode screenshot capture**, enable *Developer Mode* and *Key Server* in the TV's Developer Mode app first, then run:

```bash
python -m lg_smtv_mcp pair --fetch-ssh-key
```

Accept the pairing prompt on the TV screen using your physical remote:
- The SSAP client key is saved atomically with `0600` permissions to `CLIENT_KEY_PATH` (default `.client_key.json`).
- `--fetch-ssh-key` downloads the Developer Mode private key from `http://<TV_IP>:9991/webos_rsa` and saves it with `0600` permissions (default `.webos_rsa`). Set `SSH_KEY_PATH=.webos_rsa` (and `WEBOS_SSH_PASSPHRASE` if configured) in `.env`.

### Step 4: Start the MCP Server

```bash
# Default: persistent Streamable HTTP daemon at http://<HOST>:<PORT>/mcp
# Requires Authorization: Bearer <MCP_BEARER_TOKEN>
python -m lg_smtv_mcp serve

# Local testing over stdio (no HTTP Bearer token required)
python -m lg_smtv_mcp serve --transport stdio
```

If you re-pair while `serve` is running, send `SIGHUP` to reload `.client_key.json` without restarting the daemon.

### Step 5: Connect Your MCP Client

Point your MCP host/client at the Streamable HTTP endpoint:

- **URL:** `http://<HOST>:<PORT>/mcp` (default `http://127.0.0.1:8765/mcp`)
- **Header:** `Authorization: Bearer <MCP_BEARER_TOKEN>`

If your MCP client runs on a different machine than the server, set **both** `HOST=0.0.0.0` (or the server's LAN IP) **and** `ALLOWED_HOSTS=<server-lan-ip-or-hostname>` in `.env`, and place the server behind an HTTPS reverse proxy or encrypted tunnel (e.g., WireGuard/Tailscale) if crossing untrusted networks.

---

## Configuration Reference

Runtime configuration is loaded by [`load_config()`](src/lg_smtv_mcp/config.py) from process environment variables with `.env` fallback (existing process environment variables always take precedence).

| Variable | Purpose | Default / Requirement |
| :--- | :--- | :--- |
| `TV_IP` | Target TV IPv4 address | Required |
| `TV_MAC` | Target TV MAC address for Wake-on-LAN | Required for `tv_wake` |
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
| `CAPTURE_MAX_WIDTH` | Optional max width (`64..3840`) for host-side JPEG downscaling | Unset (raw passthrough) |
| `CAPTURE_JPEG_QUALITY` | Optional JPEG quality (`1..95`; defaults to `75` if only `CAPTURE_MAX_WIDTH` is set) | Unset (raw passthrough) |

---

## systemd Deployment

The repository includes a unit template at [`deploy/lg-smtv-mcp.service`](deploy/lg-smtv-mcp.service). It runs the server from the project checkout so `nix develop` can use the repository flake.

1. Prepare the host on the same trusted network as the TV. Install Nix with Flakes enabled, clone this repository to a stable directory, copy `.env.example` to `.env`, and set the required TV coordinates and `MCP_BEARER_TOKEN`. Keep `.env` readable by the service user and private (for example, mode `0600`).
2. Copy the template into systemd's unit directory, then replace `<User>` with the account that will run the server and `<WorkingDirectory>` with the absolute repository checkout path. Set `EnvironmentFile` to the `.env` file used for that checkout:

   ```bash
   sudo cp deploy/lg-smtv-mcp.service /etc/systemd/system/lg-smtv-mcp.service
   sudoedit /etc/systemd/system/lg-smtv-mcp.service
   ```

3. Check where Nix is installed with `command -v nix`. Replace `<NixBinDir>` in the unit's `PATH` with the directory containing that `nix` executable, keeping the standard system binary directories in the value. The unit starts `/usr/bin/env` by its absolute path; `env` then finds `nix` using the service's configured `PATH`.
4. Reload systemd and start the service:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now lg-smtv-mcp.service
   sudo systemctl status lg-smtv-mcp.service
   sudo journalctl -u lg-smtv-mcp.service
   ```

The HTTP server binds to `127.0.0.1` by default and requires `MCP_BEARER_TOKEN`. Use a trusted reverse proxy or VPN if clients need remote access. When exposing the server remotely, set **both** `HOST=0.0.0.0` (or the LAN bind address) **and** `ALLOWED_HOSTS=<proxy-or-vpn-hostname>` in `.env`; the DNS-rebinding check in `auth.py` rejects any request whose `Host` header is not in the allowlist.

### Sandboxing & Security Profile

The unit template includes a tested systemd sandboxing baseline (Issue [#17](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/issues/17)), reducing the `systemd-analyze --offline=yes security` exposure score from **9.0 UNSAFE** to **4.9 OK**:

- **Adopted Directives:**
  - `StartLimitIntervalSec=60s` / `StartLimitBurst=5`: Bounded restart budget prevents runaway restart loops on persistent environment errors (e.g. missing `.env`).
  - `NoNewPrivileges=yes`: Prevents process escalation or setuid execution.
  - `PrivateTmp=yes`: Restricts `/tmp` visibility to an isolated per-service namespace.
  - `UMask=0077`: Ensures files created by the service are private to the service user.
  - `ProtectSystem=full`: Mounts `/usr`, `/boot`, and `/etc` read-only.
  - `ProtectControlGroups=yes`, `ProtectKernelModules=yes`, `ProtectKernelTunables=yes`, `ProtectKernelLogs=yes`, `ProtectClock=yes`, `ProtectHostname=yes`: Prevents tampering with kernel and OS interfaces.
  - `RestrictRealtime=yes`, `RestrictSUIDSGID=yes`, `LockPersonality=yes`: Restricts privilege escalation vectors.
  - `CapabilityBoundingSet=`: Drops all Linux kernel capabilities.

- **Rejected Directives (Compatibility Constraints):**
  - `ProtectHome=yes` / `ProtectHome=read-only`: Ruled out because `nix develop` requires read-write access to `~/.cache/nix/fetcher-locks` for flake evaluation, and the repository checkout resides under the operator's user directory.
  - `ProtectSystem=strict`: Ruled out because it mounts the entire filesystem read-only, including the operator's home directory and the repository checkout; `nix develop` needs write access to `~/.cache/nix/fetcher-locks` for flake evaluation (the same constraint that rules out `ProtectHome`). Writable `/tmp` is not the blocker: with `PrivateTmp=yes`, systemd keeps `/tmp` and `/var/tmp` writable even under `strict`.
  - `PrivateNetwork=yes`: Ruled out because the MCP daemon must communicate over the local network to the TV via SSAP (`wss://:3001`), Developer Mode SSH/SFTP (`:9922`), and Wake-on-LAN UDP broadcasts.

### Developer Mode SSH Capture Setup

Enable Developer Mode and the TV's Key Server in the Developer Mode app. Run pairing from the configured checkout as the same service user so the client key and SSH key are saved where the service can read them:

```bash
python -m lg_smtv_mcp pair --fetch-ssh-key
```

Accept the on-screen pairing prompt. The command fetches the SSH key from the TV and saves it with mode `0600` (default path `.webos_rsa`); set `SSH_KEY_PATH` in `.env` to that path. Developer Mode provides SSH/SFTP on port `9922`. Its on-TV countdown timer eventually expires and disables Developer Mode, so screenshot capture then returns `SCREENSHOT_UNAVAILABLE` until Developer Mode is enabled again. Other SSAP tools remain available.

On a trusted home LAN, SSH host-key checking is disabled by default because the TV regenerates its SSH host key when Developer Mode is reinitialized. To pin the host key instead, set `SSH_KNOWN_HOSTS_PATH` to a `known_hosts` file and update it after Developer Mode renewals.

## Operator Live-TV E2E Smoke Test Checklist

Automated tests run strictly offline. After deploying or updating on a host on the same LAN as the TV, run this manual smoke checklist from an MCP client connected to `python -m lg_smtv_mcp serve`:

- [ ] **`pair`**: Run `python -m lg_smtv_mcp pair`, accept the on-screen TV prompt, and confirm `CLIENT_KEY_PATH` (`.client_key.json`) exists with `0600` permissions and an entry for `<TV_IP>`.
- [ ] **`tv_wake`**: With the TV in standby, verify a mutating tool (`launch`) fails fast with `TV_OFFLINE_STANDBY` (no implicit WoL), then call `tv_wake` and confirm the TV powers on and returns `status: "connected"`.
- [ ] **`get_state`**: Call `get_state` and confirm `connected: true`, `stale: false`, and live `volume`, `muted`, `sound_output`, and `foreground_app` values.
- [ ] **`launch`**: Call `launch` (e.g., `app_id="youtube.leanback.v4"` with an optional `content_target`) and confirm the app opens on screen.
- [ ] **`keys`**: Call `keys` with a small batch (e.g., `["DOWN", "ENTER"]`) and confirm D-pad navigation registers on the TV.
- [ ] **`screenshot`**: With Developer Mode active and SSH configured, call `screenshot` and confirm the response includes JPEG image content and capture metadata.
- [ ] **`keys(..., capture=True)`**: Send a small key batch with capture enabled; confirm the response reports successful key delivery and includes the screenshot. If the Developer Mode timer has expired, confirm the response reports the capture failure while preserving `keys_sent: true`.
- [ ] **`set_volume`**: Call `set_volume` with a safe level within `MAX_VOLUME` (e.g., `15`) and confirm the TV volume updates.
- [ ] **`turn_off`**: Call `turn_off` and confirm the TV enters standby and subsequent `get_state` reports `connected: false, stale: true`.

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

- [Architecture](ARCHITECTURE.md) — Canonical present-state system topology, components, and boundaries
- [Roadmap](ROADMAP.md) — Completed M1/M2 milestones and active M2.1 delivery trajectory
- [Decision Records](docs/adr/) — MADR records for core architectural choices (`0001`–`0005`)
- [Architecture & Design RFC](docs/designs/lg-smtv-mcp.md) — Component contracts, state machine, error taxonomy, and implementation plan
- [E2E Benchmarks](docs/experiments/crunchyroll-e2e-comparison.md) — Standardized live-TV Crunchyroll navigation telemetry (baseline vs. post-`#20`)
- [Verified Capabilities](docs/poc/capabilities.md) — Empirical SSAP, DIAL, deep-link, and Developer Mode findings
- [Crunchyroll E2E Experiment](docs/poc/crunchyroll-e2e-experiment.md) — Initial vision-guided navigation experiment and latency measurements
- [Agent & Engineering Guidelines](AGENTS.md) — Coding standards, validation boundaries, protocol invariants, and CI rules

---

## License

Released into the public domain under the [Unlicense](LICENSE).
