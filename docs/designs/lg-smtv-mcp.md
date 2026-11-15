# lg-smtv-mcp

*   **Author:** kiskaadee
*   **Status:** Draft
*   **Date:** 2026-11-15

--------------------------------------------------------------------------------

## Overview

`lg-smtv-mcp` is a Python Model Context Protocol (MCP) server for local-network control of a single pre-paired LG webOS Smart TV. It runs primarily as a long-lived `streamable-http` daemon on a homelab server on the same LAN (with `stdio` supported for local testing), keeping the Simple Service Access Protocol (`SSAP`, LG webOS's local JSON-over-WebSocket control protocol on `wss://<TV_IP>:3001`), pointer input socket, `subscribe` state cache, and optional [LG Developer Mode](https://webostv.developer.lg.com/develop/getting-started/developer-mode-app) `asyncssh`/SFTP session (`prisoner@<TV_IP>:9922`) warm. The server contains no LLM or vision-model calls: UI navigation loops live in the MCP host (such as Antigravity, Claude, or a closed-set action-router model like `gemini-decision-1`), while `lg-smtv-mcp` exposes deterministic, decision-model-friendly tools and composite actions (such as batched key sequences with optional inline `ImageContent` screenshots).

## The Problem

Controlling an LG webOS TV from an AI agent or automation host using one-shot CLI scripts introduces prohibitive latency and reliability gaps (empirically documented in [docs/poc/capabilities.md](../poc/capabilities.md) and [docs/poc/crunchyroll-e2e-experiment.md](../poc/crunchyroll-e2e-experiment.md) against `WebOS/4.1.0`):

1. **Per-step handshake overhead:** In the Crunchyroll PoC ([docs/poc/crunchyroll-e2e-experiment.md](../poc/crunchyroll-e2e-experiment.md)), navigating from app launch to episode playback took 13 steps and ~7 minutes (`427s`), largely because each step spawned a fresh process, `wss://:3001` TLS + `register` handshake, `getPointerInputSocket` connection, and `scp` session.
2. **Protocol hazards on persistent connections:** Running `subscribe` streams (`audio/getStatus`, `getForegroundAppInfo`) concurrently with tool calls breaks one-shot `ws.recv()` patterns. Additionally, when the TV is in standby, `wss://:3001` accepts the WebSocket upgrade and closes immediately with `1008 (Try Again Later (EWS))`, and `ime/insertText` silently returns `returnValue: true` even when the native keyboard is closed.
3. **Unbounded token and `/tmp` costs:** Without batched key execution, inline image return, and deterministic frame-change detection (`frame_sha256`), the host pays an extra round-trip and vision-model turn on unchanged frames. Without per-call `/tmp` cleanup, repeated captures can exhaust the TV's ~1.2 GB RAM.

`lg-smtv-mcp` solves this by keeping persistent, serialized connections inside a homelab `streamable-http` daemon, failing fast on `1008` standby, degrading cleanly when the TV's Developer Mode SSH session timer expires, and returning compact state/image payloads.

## Goals

*   Run a 24/7 `streamable-http` MCP daemon bound to a configurable `HOST` address (defaulting to `127.0.0.1` locally, or the homelab LAN interface when deployed) with constant-time `Authorization: Bearer <token>` authentication, plus `stdio` transport for local test harnesses.
*   Maintain persistent SSAP (`wss://:3001`), pointer input socket, `subscribe` state cache, and optional `asyncssh`/SFTP (`:9922`) connections inside a unified `tv_core` package.
*   Serialize stateful UI and capture operations (`keys`, `type_text`, `screenshot`) with bounded contention while keeping read-only state queries non-blocking.
*   Fail fast on WebSocket close code `1008` (`TV_OFFLINE_STANDBY`) for mutating tools, return `connected: false, stale: true` with `last_known` values for read-only queries, and provide an explicit `tv_wake` tool using configurable Wake-on-LAN broadcast.
*   Expose decision-model-friendly composite tools (`keys` with delays and optional inline `ImageContent`, `type_text`, `launch` with deep-link helpers, `screenshot` with `frame_sha256` and `changed_since_last`, `set_volume` with configurable `max_volume=40` cap, explicit `turn_off` / `switch_input`, and opt-in `ENABLE_RAW_SSAP=true`).
*   Provide a one-time CLI `pair` command for interactive on-screen `PROMPT` registration and `client-key` persistence.
*   Verify all behavior offline via `pytest` contract tests over `Protocol` mocks, passing `ruff` and `pyright` inside `nix develop`.

## Non-Goals

*   Embedding LLM/VLM API keys, outbound model calls, or an internal vision-navigation loop inside `lg-smtv-mcp`.
*   Adding an extra HTTP or IPC wrapper service behind the MCP server.
*   Exposing arbitrary SSH command execution on the TV or retaining screenshot files in `/tmp`.
*   Multi-TV fleet management or interactive `PROMPT` pairing inside MCP tool calls.
*   Implicitly waking the TV on normal control or state-query calls.

## Open Questions

None. All architectural questions from the initial RFC have been resolved.

## Critical User Journeys (CUJs)

### CUJ 1: Standby Detection and Explicit Wake

*   **Given**: The TV is in standby (`wss://<TV_IP>:3001` closes with `1008 Try Again Later (EWS)`), and the daemon is running in `STANDBY_OFFLINE` state.
*   **When**: An MCP client calls `launch("youtube.leanback.v4")`.
*   **Then**: The tool fails fast with `TV_OFFLINE_STANDBY` without sending a Wake-on-LAN packet. The client then calls `tv_wake`, which broadcasts the WoL magic packet to `<TV_MAC>` on the configured broadcast address/port, polls until `1008` clears and `register` + `subscribe` succeed, and returns the online status.

### CUJ 2: Vision-Guided UI Navigation with Batched Keys and Change Detection

*   **Given**: The TV is online and Developer Mode SSH (`:9922`) is active.
*   **When**: A host decision loop calls `keys(["LEFT", "UP", "ENTER"], delay_ms=250, settle_ms=1000, capture=True)`.
*   **Then**: `tv_core` serializes the operation, sends the three button frames over the persistent pointer socket, waits `settle_ms`, captures and cleans up the remote screenshot into memory, and returns `keys_sent: true`, inline `ImageContent`, `frame_sha256`, and `changed_since_last`.

### CUJ 3: Graceful Degradation When Developer Mode SSH Expires

*   **Given**: The TV is online, SSAP and pointer sockets are connected, and Developer Mode SSH (`:9922`) is disabled or expired.
*   **When**: A client calls `keys(["DOWN", "ENTER"], capture=True)` or `set_volume(15)`.
*   **Then**: `set_volume(15)` succeeds normally. `keys(..., capture=True)` executes the keystrokes and returns partial success (`keys_sent: true`, `capture_error: "SCREENSHOT_UNAVAILABLE: ..."`) so the caller knows not to re-send the keys.

### CUJ 4: One-Time CLI Pairing

*   **Given**: A new or reset TV has no saved `client-key` on the homelab server.
*   **When**: The operator runs `lg-smtv-mcp pair --ip <TV_IP>` from the terminal and accepts the on-screen `PROMPT` with the physical remote.
*   **Then**: The CLI saves the returned `client-key` to the configured key file path, allowing the `streamable-http` daemon to start and register non-interactively.

## Technical Plan (Architecture)

### 1. Package & Module Layout

All runtime code lives under `src/lg_smtv_mcp/`, separating the transport/protocol boundaries and state machine (`tv_core`) from the MCP tool surface (`server.py`), HTTP Bearer auth (`auth.py`), and CLI entrypoints (`cli.py`).

| Module | Responsibility |
| :--- | :--- |
| `src/lg_smtv_mcp/config.py` *(planned)* | Typed `TvConfig` loaded from env vars / `.env` (`TV_IP`, `TV_MAC`, `WOL_BROADCAST`, `CLIENT_KEY_PATH`, `SSH_KEY_PATH`, `WEBOS_SSH_PASSPHRASE`, `HOST`, `PORT`, `MCP_BEARER_TOKEN`, `MAX_VOLUME`, `ENABLE_RAW_SSAP`). |
| `src/lg_smtv_mcp/errors.py` *(planned)* | Typed domain exceptions and error codes (`TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED`, `SCREENSHOT_UNAVAILABLE`, `LOCK_TIMEOUT`, `WAKE_TIMEOUT`, `INVALID_ARGUMENT`). |
| `src/lg_smtv_mcp/tv_core/protocols.py` *(planned)* | Thin Python `Protocol` interfaces (`WebSocketTransport`, `CaptureTransport`) enabling offline contract tests. |
| `src/lg_smtv_mcp/tv_core/ssap.py` *(planned)* | `SsapClient`: single background reader task routing frames by `msg["id"]` to `asyncio.Future`s and `subscribe` callbacks; `PERMISSIONS` constant; `register` + `1008` detection. |
| `src/lg_smtv_mcp/tv_core/pointer.py` *(planned)* | `PointerClient`: acquires `getPointerInputSocket`, sends plain-text `type:button` frames, and transparently re-acquires the socket if dropped. |
| `src/lg_smtv_mcp/tv_core/capture.py` *(planned)* | `CaptureClient`: persistent `asyncssh` SFTP session (`config=[]`, `ssh-rsa` host/signature algs, passwordless key or `WEBOS_SSH_PASSPHRASE`), triggers `ssap://com.webos.service.capture/executeOneShot` (`{"path": "/tmp/lg_smtv_cap_<seq>.jpg", "method": "DISPLAY", "format": "JPG"}`), pulls into memory under a shared `6s` deadline with a `1–2s` `finally: sftp.remove()` grace period, and tracks `frame_sha256` / `changed_since_last`. |
| `src/lg_smtv_mcp/tv_core/manager.py` *(planned)* | `TvManager`: single-owner connection state machine, `send_wol` UDP magic packet helper (`WOL_BROADCAST`, port `9`), `subscribe` state cache (`audio/getStatus`, `getForegroundAppInfo`), asymmetric reconnect loop, `CaptureClient` teardown on `STANDBY_OFFLINE`, `.client_key.json` reload on `PAIRING_REQUIRED` backoff/`SIGHUP`, and `asyncio.Lock` (10s timeout) serializing UI/capture operations. |
| `src/lg_smtv_mcp/auth.py` *(planned)* | Minimal ASGI Bearer token middleware (`hmac.compare_digest` on `Authorization: Bearer <token>`) wrapping FastMCP's `streamable_http_app()` and `TransportSecuritySettings`. |
| `src/lg_smtv_mcp/server.py` *(planned)* | FastMCP tool definitions, `ImageContent` formatting, partial-success shaping, `max_volume` enforcement, and `ENABLE_RAW_SSAP` gating. |
| `src/lg_smtv_mcp/cli.py` *(planned)* | CLI entrypoint supporting `serve` (FastMCP `streamable-http` or `stdio`) and one-time interactive `pair` (atomic `os.replace` write to `.client_key.json`). |

```mermaid
classDiagram
    class TvManager {
        +state: ConnectionState
        +wake() TvStatus
        +get_state() TvStateSnapshot
        +send_keys(keys, delay_ms, settle_ms, capture) KeysResult
        +type_text(text, send_enter, capture) TextResult
        +screenshot() CaptureResult
        +launch_app(app_id, content_target, params) dict
        +set_volume(level) dict
        +set_mute(muted) dict
        +switch_input(input_id) dict
        +turn_off() dict
        +raw_ssap(uri, payload) dict
    }
    class SsapClient {
        +connect_and_register(client_key) str
        +request(uri, payload) dict
        +subscribe(uri, callback) void
        +close() void
    }
    class PointerClient {
        +send_buttons(keys, delay_ms) int
        +close() void
    }
    class CaptureClient {
        +capture_frame(ssap_request_fn) CaptureResult
        +close() void
    }
    class WebSocketTransport {
        <<interface>>
    }
    class CaptureTransport {
        <<interface>>
    }
    TvManager --> SsapClient
    TvManager --> PointerClient
    TvManager --> CaptureClient
    SsapClient --> WebSocketTransport
    PointerClient --> WebSocketTransport
    CaptureClient --> CaptureTransport
```

### 2. Connection State Machine, Frame Dispatch & Concurrency

```mermaid
stateDiagram-v2
    [*] --> CONNECTING: Daemon startup
    CONNECTING --> CONNECTED: register succeeds and subscribe active
    CONNECTING --> STANDBY_OFFLINE: WS close 1008 or connect timeout
    CONNECTING --> PAIRING_REQUIRED: register returns PROMPT
    CONNECTED --> STANDBY_OFFLINE: Socket closed / 1008
    STANDBY_OFFLINE --> WAKING: Explicit tv_wake tool called
    STANDBY_OFFLINE --> CONNECTING: Asymmetric backoff
    WAKING --> CONNECTED: Poll succeeds and register active
    WAKING --> STANDBY_OFFLINE: Wake poll timeout
    PAIRING_REQUIRED --> CONNECTING: Backoff tick or SIGHUP reloads key file
```

*   **SSAP `msg["id"]` Multiplexer:** `SsapClient` runs one background reader task that owns `ws.recv()`. Responses to one-shot `request()` calls resolve matching `asyncio.Future`s by `msg["id"]`; subscription `response` updates (`audio/getStatus`, `getForegroundAppInfo`) update the in-memory `TvStateSnapshot` in `TvManager`.
*   **Standby & Fail-Fast:** WebSocket close code `1008` (`Try Again Later (EWS)`) or a ~1.5–2s connect timeout transitions `TvManager` to `STANDBY_OFFLINE` and tears down any open `CaptureClient` SSH/SFTP session (which reconnects lazily on the next capture) so no half-open `:9922` socket lingers across standby. Mutating tools fail fast with `TV_OFFLINE_STANDBY` without triggering WoL. Read-only queries (`get_state`) bypass `ui_lock` and return `connected: false, stale: true` with `last_known` values.
*   **Reconnect, Wake & Key Reload:** In `STANDBY_OFFLINE`, a single background task retries with asymmetric backoff (`2s -> 5s -> 15s -> 30s`) to detect manual power-ons within seconds. Calling `tv_wake` sends the WoL magic packet and switches to ~1s polling until `1008` clears and `register` + `subscribe` complete. In `PAIRING_REQUIRED`, `TvManager` re-checks `CLIENT_KEY_PATH` (`{ip: client_key}` JSON, written atomically via `os.replace` by `cli.py pair`) on its backoff tick or `SIGHUP` so pairing requires no manual daemon restart.
*   **UI/Capture Serialization & Timeout Budget:** `send_keys`, `type_text`, and `screenshot` acquire `ui_lock` (`asyncio.Lock`) with a 10s timeout (`LOCK_TIMEOUT` if exceeded) so concurrent callers cannot interleave pointer frames or contend for SFTP capture. `CaptureClient.capture_frame()` wraps the entire SSH connect + `ssap://com.webos.service.capture/executeOneShot` + SFTP read sequence in a single shared `6s` deadline (`asyncio.timeout(6.0)`) and bounds the `finally: sftp.remove()` cleanup to a `1–2s` grace period—keeping worst-case capture hold time under `8s` (strictly below the `10s` `ui_lock` budget) so a stalled `:9922` socket cannot starve subsequent SSAP or pointer calls.

### 3. MCP Tool Surface

All tools return structured JSON payloads (plus inline MCP `ImageContent` when `capture=True` or `screenshot` succeeds).

| Tool | Inputs | Output / Behavior | Error Codes |
| :--- | :--- | :--- | :--- |
| `tv_wake` | *(none)* | Sends WoL to `<TV_MAC>` via `WOL_BROADCAST`; polls until `CONNECTED`; returns `{status, elapsed_ms}`. | `WAKE_TIMEOUT` |
| `get_state` | *(none)* | Lock-free read of `subscribe` cache (`connected`, `stale`, `volume`, `muted`, `sound_output`, `foreground_app`, `last_updated`). | *(never fails on standby)* |
| `keys` | `keys: list[str]`, `delay_ms: int = 250`, `settle_ms: int = 800`, `capture: bool = False` | Acquires `ui_lock`; sends pointer buttons; if `capture=True`, captures frame. On SSH failure after keys succeed, returns `{keys_sent: true, count, capture_error}` (partial success). | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED`, `LOCK_TIMEOUT` |
| `type_text` | `text: str`, `send_enter: bool = True`, `capture: bool = False` | Acquires `ui_lock`; calls `ime/insertText` + optional `sendEnterKey` (requires native on-screen keyboard to be open); if `capture=True`, captures frame (returning `{text_sent: true, capture_error}` partial success on SSH failure). | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED`, `LOCK_TIMEOUT` |
| `screenshot` | *(none)* | Acquires `ui_lock`; delegates to `CaptureClient.capture_frame()`; returns `ImageContent` + `{frame_sha256, changed_since_last, size_bytes}`. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED`, `SCREENSHOT_UNAVAILABLE`, `LOCK_TIMEOUT` |
| `launch` | `app_id: str`, `content_target: str \| None = None`, `params: dict \| None = None` | Calls `ssap://system.launcher/launch` with `{"id": app_id, "params": ...}`. When `content_target` is provided, sets `params["contentTarget"] = content_target` (e.g., `"v=<id>&t=<sec>s"` for YouTube `youtube.leanback.v4` or `"m=https://www.netflix.com/title/<id>"` for Netflix; see [docs/poc/capabilities.md](../poc/capabilities.md)). | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED` |
| `list_apps` | *(none)* | Calls `com.webos.applicationManager/listLaunchPoints`; returns compact `[{id, title}]`. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED` |
| `set_volume` | `level: int` | Rejects with `INVALID_ARGUMENT` if `level < 0` or `level > MAX_VOLUME` (default `40`); otherwise calls `audio/setVolume`. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED`, `INVALID_ARGUMENT` |
| `set_mute` | `muted: bool` | Calls `audio/setMute`. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED` |
| `list_inputs` | *(none)* | Calls `tv/getExternalInputList`; returns `[{id, label, connected}]`. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED` |
| `switch_input` | `input_id: str` | Explicit input switch via `tv/switchInput`. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED` |
| `turn_off` | *(none)* | Explicit power off via `system/turnOff`. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED` |
| `raw_ssap` | `uri: str`, `payload: dict \| None = None` | Registered **only** when `ENABLE_RAW_SSAP=true`; forwards single SSAP request. | `TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED` |

### 4. Runtime Configuration, Security & Daemon Deployment

*   **Network & Auth:** `streamable-http` binds to `HOST` (default `127.0.0.1` for local dev; `<SERVER_IP>` on the LAN when deployed) and `PORT` (default `8765`). Requests require `Authorization: Bearer <token>` verified via `hmac.compare_digest` against `MCP_BEARER_TOKEN`. MCP `TransportSecuritySettings` restricts `Host` / `Origin` headers to configured local hostnames/IPs.
*   **Developer Mode SSH Lifecycle & Headless `asyncssh` Keying:** Retail LG webOS TVs do not serve `/tmp` screenshots over HTTPS (`:3001`), so `CaptureClient` pulls captures over SFTP from the unprivileged SSH server (`prisoner@<TV_IP>:9922`) provided by LG's official [Developer Mode app](https://webostv.developer.lg.com/develop/getting-started/developer-mode-app) (see [docs/poc/capabilities.md](../poc/capabilities.md)). Port `:9922` is available only while "Dev Mode Status" and "Key Server" are enabled on the TV, and LG automatically disables Developer Mode if its countdown timer expires (at which point `CaptureClient` raises `SCREENSHOT_UNAVAILABLE` while all SSAP and WoL tools continue operating normally). On a `systemd` service without an interactive TTY or `ssh-agent`, `CaptureClient` connects with `config=[]`, legacy `server_host_key_algs=["ssh-rsa", ...]`, `signature_algs=["ssh-rsa", ...]`, and either a dedicated passwordless service key (`SSH_KEY_PATH`) or `WEBOS_SSH_PASSPHRASE` passed to `asyncssh.connect(..., passphrase=...)`.
*   **WoL Broadcast:** `WOL_BROADCAST` defaults to `255.255.255.255` and can be set to the LAN subnet broadcast (e.g., `<LAN_SUBNET_BROADCAST>`) on multi-homed hosts so magic packets exit on the TV's Ethernet/Wi-Fi segment.

### 5. Offline Testing & Nix Environment

*   **Offline Contract Tests:** Tests inject fake `WebSocketTransport` and `CaptureTransport` implementations (plus a fake `send_wol` callback) into `TvManager` to deterministically test: `1008` fail-fast vs. `tv_wake` recovery, `CaptureClient` teardown on `STANDBY_OFFLINE`, `msg["id"]` interleaving between `request()` and `subscribe`, pointer socket drop + auto-reacquire, `ui_lock` contention timeout, `CaptureClient` `6s` deadline + `1–2s` cleanup timeout isolation, `keys(..., capture=True)` and `type_text(..., capture=True)` partial success when SSH fails, `finally: sftp.remove()` cleanup on failure, `frame_sha256` / `changed_since_last`, `PAIRING_REQUIRED` key reload on backoff/`SIGHUP`, `MAX_VOLUME` guardrail, `ENABLE_RAW_SSAP` gating, and Bearer auth rejection.
*   **`flake.nix` Updates:** Expand `pythonEnv` to include `mcp` (which transitively provides Starlette/Uvicorn for `FastMCP`'s built-in `streamable-http` server), `asyncssh`, `pytest`, and `pytest-asyncio` alongside `websockets`, `pyright`, and `ruff`.

## Alternatives Considered

### Alternative A: Single-Process `stdio` Per Client Only

*   **Pros:** Zero HTTP server, port binding, or Bearer token configuration required.
*   **Cons:** Every client session spawns a new process, repeating `wss://:3001` TLS + `register`, `getPointerInputSocket`, and `:9922` SSH handshakes; concurrent clients contend for the TV's single pointer socket.
*   **Reason for Rejection:** Rejected by author: `streamable-http` keeps all TV transports and the `subscribe` state cache warm 24/7 while `stdio` remains available for local test harnesses.

### Alternative B: In-Server Vision Navigation Sub-Loop

*   **Pros:** Could encapsulate multi-step UI navigation behind a single high-level natural-language tool call.
*   **Cons:** Couples a local hardware driver to external LLM/VLM API keys, provider SDKs, model selection, and prompt engineering.
*   **Reason for Rejection:** Rejected by author: `lg-smtv-mcp` stays strictly LLM-free; latency and token overhead are addressed instead via composite tools (`keys(..., capture=True)`) and deterministic `frame_sha256` / `changed_since_last` metadata.

### Alternative C: OpenSSH `ControlMaster` Subprocess for Screenshots

*   **Pros:** Uses system OpenSSH directly for legacy `ssh-rsa` compatibility.
*   **Cons:** Requires managing background `ssh` control sockets, parsing subprocess output/exit codes, and complicates offline unit testing.
*   **Reason for Rejection:** Rejected by author: `asyncssh` (`2.24.0`) was empirically verified on Python 3.14 against `:9922` (`config=[]`, explicit `ssh-rsa` algorithms) and fits cleanly behind an async `Protocol` mock.

### Alternative D: Fixed `/tmp/test.jpg` Overwrite Path

*   **Pros:** Bounded to a single file on `/tmp` without explicit delete calls.
*   **Cons:** If `executeOneShot` fails silently or races, SFTP pull can read a stale screenshot from an earlier step.
*   **Reason for Rejection:** Rejected by author: capturing to `/tmp/lg_smtv_cap_<seq>.jpg`, pulling only when `returnValue: true`, and calling `sftp.remove()` in `finally:` prevents both stale reads and `/tmp` exhaustion on the TV's ~1.2 GB RAM.

### Alternative E: Unconditional `raw_ssap` Tool

*   **Pros:** Allows calling arbitrary SSAP URIs at any time without code changes.
*   **Cons:** Expands the tool schema and weakens guardrails (`max_volume`, explicit `turn_off`) for closed-set decision models.
*   **Reason for Rejection:** Rejected by author: `raw_ssap` is omitted by default and registered only when `ENABLE_RAW_SSAP=true` is explicitly configured.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Security & Privacy:** `streamable-http` binds to configurable `HOST` (default `127.0.0.1`, or `<SERVER_IP>` on private LAN), enforces `Authorization: Bearer <token>` via `hmac.compare_digest`, and configures MCP `TransportSecuritySettings`. Arbitrary SSH command execution is never exposed. `.client_key.json`, `.env`, SSH keys, and `*.jpg` captures are excluded from version control.
*   **Performance & Latency:** Persistent SSAP, pointer, and `asyncssh` SFTP connections eliminate per-step TLS/SSH handshakes (~8–10s/step in PoC); batched `keys(..., capture=True)` and `frame_sha256` / `changed_since_last` eliminate redundant host vision turns.
*   **Operational Resilience:** WebSocket `1008` fails fast (`TV_OFFLINE_STANDBY`), `ui_lock` times out cleanly at 10s (`LOCK_TIMEOUT`), and Developer Mode SSH expiration degrades only `screenshot` / `capture=True` without disrupting SSAP or WoL tools.

## Detailed Implementation Plan

### Target Files to Create/Modify

#### 1. `flake.nix` (Modify)

*   **Type / Scope:** Nix development shell configuration.
*   **Proposed Changes:** Add `mcp`, `asyncssh`, `pytest`, and `pytest-asyncio` to `pythonEnv` alongside `websockets`, `pkgs.pyright`, and `pkgs.ruff`. Verify Python 3.14 evaluation via `nix develop`.

#### 2. `pyproject.toml` (Create)

*   **Type / Scope:** Python package metadata, build, and tool configuration.
*   **Proposed Changes:** Define `lg-smtv-mcp` package under `src/`, console script entrypoint `lg-smtv-mcp = lg_smtv_mcp.cli:main`, `pytest` (`asyncio_mode = "auto"`), `ruff`, and `pyright` (`src/` + `tests/` paths, strict/standard type-checking).

#### 3. `.gitignore` and `.env.example` (Modify / Create)

*   **Type / Scope:** Secret hygiene and runtime configuration template.
*   **Proposed Changes:** Ensure `.gitignore` excludes `.client_key.json`, `.env`, `*.jpg`, and SSH keys so no personal LAN IPs, MACs, keys, or captures are committed (per [AGENTS.md](../../AGENTS.md) Section 4). Create `.env.example` with placeholder values (`TV_IP=<TV_IP>`, `TV_MAC=<TV_MAC>`, `WOL_BROADCAST=255.255.255.255`, `CLIENT_KEY_PATH=.client_key.json`, `SSH_KEY_PATH=`, `WEBOS_SSH_PASSPHRASE=`, `HOST=127.0.0.1`, `PORT=8765`, `MCP_BEARER_TOKEN=<GENERATE_TOKEN>`, `MAX_VOLUME=40`, `ENABLE_RAW_SSAP=false`).

#### 4. `src/lg_smtv_mcp/__init__.py` and `src/lg_smtv_mcp/errors.py` (Create)

*   **Type / Scope:** Package root and typed domain error taxonomy.
*   **Proposed Changes:** Define `TvError(Exception)` carrying `code: ErrorCode` (`TV_OFFLINE_STANDBY`, `PAIRING_REQUIRED`, `SCREENSHOT_UNAVAILABLE`, `LOCK_TIMEOUT`, `WAKE_TIMEOUT`, `INVALID_ARGUMENT`) and `message: str` so MCP tools map failures deterministically.

#### 5. `src/lg_smtv_mcp/config.py` (Create)

*   **Type / Scope:** Runtime configuration loader.
*   **Proposed Changes:** Define frozen `TvConfig` dataclass and `load_config()` reading environment variables / `.env` without hardcoded personal IPs, MACs, or paths. Validate `HOST`, `PORT`, `MAX_VOLUME` (default `40`), `WOL_BROADCAST`, `MCP_BEARER_TOKEN` (required for `streamable-http`), and `ENABLE_RAW_SSAP` (default `False`).

#### 6. `src/lg_smtv_mcp/tv_core/protocols.py` (Create)

*   **Type / Scope:** Transport abstraction boundaries (`Protocol`s).
*   **Proposed Changes:** Define `WebSocketTransport` (shared by `SsapClient` and `PointerClient`) and `CaptureTransport` `Protocol` interfaces so `tv_core` depends on abstractions and `pytest` injects deterministic fakes offline.

#### 7. `src/lg_smtv_mcp/tv_core/ssap.py` (Create)

*   **Type / Scope:** SSAP WebSocket client and frame multiplexer.
*   **Proposed Changes:** Implement `SsapClient` with shared `PERMISSIONS` constant (always sent in `register`, even when reusing `client-key`), self-signed TLS context, `1008` / connect-timeout detection (`TV_OFFLINE_STANDBY`), `PROMPT` detection (`PAIRING_REQUIRED` in daemon mode; interactive wait in CLI `pair`), and a single background `_reader_loop()` routing frames by `msg["id"]` to pending `asyncio.Future`s or `subscribe` handlers.

#### 8. `src/lg_smtv_mcp/tv_core/pointer.py` (Create)

*   **Type / Scope:** Pointer input WebSocket client.
*   **Proposed Changes:** Implement `PointerClient` calling `com.webos.service.networkinput/getPointerInputSocket`, formatting plain-text `type:button\nname:<KEY>\n\n` frames, pacing batches by `delay_ms`, and transparently re-acquiring the socket if closed independently of SSAP.

#### 9. `src/lg_smtv_mcp/tv_core/capture.py` (Create)

*   **Type / Scope:** Developer Mode `asyncssh`/SFTP screenshot client.
*   **Proposed Changes:** Implement `CaptureClient` holding a persistent `asyncssh` SFTP connection (`prisoner@<TV_IP>:9922`, `config=[]`, `server_host_key_algs=["ssh-rsa", ...]`, `signature_algs=["ssh-rsa", ...]`, optional `WEBOS_SSH_PASSPHRASE`). On capture: wrap SSH connect + `ssap://com.webos.service.capture/executeOneShot` (`{"path": "/tmp/lg_smtv_cap_<seq>.jpg", "method": "DISPLAY", "format": "JPG"}`) + SFTP read in a single shared `6s` deadline (`asyncio.timeout(6.0)`), pull into memory only if `returnValue: true`, run `await sftp.remove(remote_path)` in `finally:` under a `1–2s` cleanup timeout, compute `frame_sha256` and `changed_since_last`, and raise `SCREENSHOT_UNAVAILABLE` if Developer Mode SSH is disabled/expired.

#### 10. `src/lg_smtv_mcp/tv_core/manager.py` and `src/lg_smtv_mcp/tv_core/__init__.py` (Create)

*   **Type / Scope:** Single-owner connection state machine, `subscribe` cache, and WoL helper.
*   **Proposed Changes:** Implement `send_wol(mac, broadcast_addr, port=9)` UDP magic packet helper and `TvManager` managing `ConnectionState` (`CONNECTING`, `CONNECTED`, `STANDBY_OFFLINE`, `WAKING`, `PAIRING_REQUIRED`), `subscribe` cache (`audio/getStatus`, `getForegroundAppInfo`), asymmetric reconnect backoff (`2s -> 5s -> 15s -> 30s`), `CaptureClient` teardown on `STANDBY_OFFLINE`, `CLIENT_KEY_PATH` (`{ip: client_key}`) reload on `PAIRING_REQUIRED` backoff ticks or `SIGHUP`, `ui_lock` (`asyncio.Lock` with 10s acquire timeout), lock-free `get_state()` returning `connected: false, stale: true` + `last_known` when offline, and partial-success handling on `send_keys(..., capture=True)` and `type_text(..., capture=True)`.

#### 11. `src/lg_smtv_mcp/auth.py` (Create)

*   **Type / Scope:** Minimal HTTP Bearer token middleware and transport security.
*   **Proposed Changes:** Implement a minimal ASGI middleware verifying `Authorization: Bearer <token>` via `hmac.compare_digest` (returning `401` on missing/invalid token) around FastMCP's built-in `streamable_http_app()` and configure MCP `TransportSecuritySettings`.

#### 12. `src/lg_smtv_mcp/server.py` (Create)

*   **Type / Scope:** FastMCP server and tool definitions.
*   **Proposed Changes:** Register all 12 default tools (`tv_wake`, `get_state`, `keys`, `type_text`, `screenshot`, `launch`, `list_apps`, `set_volume`, `set_mute`, `list_inputs`, `switch_input`, `turn_off`) and conditionally register `raw_ssap` only when `ENABLE_RAW_SSAP=true`. Document the `type_text` native-keyboard requirement in its tool description, enforce `0 <= level <= MAX_VOLUME`, and format inline `ImageContent` + metadata.

#### 13. `src/lg_smtv_mcp/cli.py` (Create)

*   **Type / Scope:** CLI entrypoint (`serve`, `pair`).
*   **Proposed Changes:** Implement `serve --transport [streamable-http|stdio]` (delegating to FastMCP's built-in `streamable-http` app wrapped with `auth.py`, or `FastMCP.run(transport="stdio")`) and `pair --ip <TV_IP>` for one-time interactive `PROMPT` pairing with atomic `os.replace` persistence to `.client_key.json` (`{ip: client_key}`).

#### 14. `tests/` (`conftest.py`, `test_ssap.py`, `test_manager.py`, `test_capture.py`, `test_server_auth.py`) (Create)

*   **Type / Scope:** Offline `pytest` unit and contract test suite.
*   **Proposed Changes:** Provide `WebSocketTransport` and `CaptureTransport` fakes (plus a `send_wol` stub) and verify: `msg["id"]` multiplexing with interleaved `subscribe` updates; `1008` fail-fast vs. `tv_wake` recovery; `CaptureClient` teardown on `STANDBY_OFFLINE`; offline `get_state` (`connected: false, stale: true`); `ui_lock` 10s timeout and `CaptureClient` `6s` deadline + `1–2s` cleanup timeout isolation; pointer auto-reacquire; `keys(..., capture=True)` and `type_text(..., capture=True)` partial success when SSH fails; `finally: sftp.remove()` cleanup; `frame_sha256` / `changed_since_last`; `PAIRING_REQUIRED` key reload on backoff/`SIGHUP`; `set_volume` `INVALID_ARGUMENT` above `MAX_VOLUME`; `ENABLE_RAW_SSAP` gating; and Bearer auth `401`/constant-time check.

#### 15. `deploy/lg-smtv-mcp.service` and `README.md` (Create / Modify)

*   **Type / Scope:** Deployment template and repository documentation.
*   **Proposed Changes:** Add a placeholder `systemd` unit file (`deploy/lg-smtv-mcp.service`) referencing `EnvironmentFile` and `nix develop --command lg-smtv-mcp serve`, and update `README.md` with setup, `pair`, `.env`, and verification instructions.

### Verification Plan

*   **Environment & Build:** `nix develop --command python -c "import lg_smtv_mcp"`
*   **Static Analysis & Linting:** `nix develop --command ruff check .` and `nix develop --command pyright`
*   **Offline Unit & Contract Tests:** `nix develop --command pytest` (100% offline against `Protocol` mocks; zero packets sent to physical hardware)
*   **Manual Live-TV Integration Checks:** Explicit operator-run verification of `pair`, `tv_wake` from `1008` standby, `keys(..., capture=True)` with `asyncssh`, and `set_volume` cap against `<TV_IP>`.
