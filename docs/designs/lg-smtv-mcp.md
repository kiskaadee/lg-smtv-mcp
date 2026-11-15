# lg-smtv-mcp

* **Author:** kiskaadee
* **Date:** 2026-11-15

## Overview

`lg-smtv-mcp` is a Python Model Context Protocol (MCP) server for controlling a single pre-paired LG webOS Smart TV over the local network. It runs primarily as a long-lived `streamable-http` daemon on a homelab server on the same LAN (with `stdio` supported for local testing), keeping SSAP WebSocket, pointer input socket, `subscribe` state cache, and optional Developer Mode `asyncssh`/SFTP connections warm. It stays strictly LLM-free: vision-guided UI navigation lives in the MCP host (such as Antigravity, Claude, or a `gemini-decision-1` loop), while the server exposes high-level, decision-model-friendly tools and composite actions (such as batched key sequences with optional inline `ImageContent` screenshots).

## Analysis

### Core Architecture & Connection Lifecycle

1. **Central SSAP Frame Dispatcher:**
   The PoC script (`docs/poc/tv_test.py`) uses one-shot `await ws.recv()` calls. In a persistent daemon that holds active `subscribe` streams (`audio/getStatus`, `getForegroundAppInfo`) alongside tool requests, a single background reader task owns `ws.recv()` and routes incoming frames by `msg["id"]` to pending `asyncio.Future`s while dispatching subscription push updates to the state cache.

2. **Single-Owner Connection State Machine & Standby Handling:**
   Empirically, when the TV is in standby or booting, `wss://192.168.1.11:3001` accepts the TLS/WebSocket upgrade and immediately closes with `1008 (policy violation) Try Again Later (EWS)`. `tv_core` uses an explicit state machine (`STANDBY_OFFLINE` -> `CONNECTING` -> `CONNECTED` / `WAKING`) so that `tv_wake` polling and background reconnect never race on `wss://:3001` or double-register. Mutating tools treat `1008` (or a ~1.5–2s connect timeout) as an immediate `TV_OFFLINE_STANDBY` error rather than blocking or auto-waking the TV.

3. **Pointer Socket Auto-Recovery & Daemon Pairing Guard:**
   `getPointerInputSocket` opens a secondary `wss://` socket for plain-text button frames (`type:button`). If the TV drops the pointer socket independently of the main SSAP socket, `keys` transparently re-acquires it rather than failing the call. Because `getPointerInputSocket` requires `manifest.permissions` even when reusing a saved `client-key`, `PERMISSIONS` is pinned as a shared constant between the CLI `pair` command and the daemon. If the TV responds to `register` with `pairingType: "PROMPT"` during daemon operation, the daemon fails fast with `PAIRING_REQUIRED` instead of hanging for a human with a physical remote.

4. **Read vs. Write Concurrency & Offline State:**
   Stateful UI and capture operations (`keys`, `type_text`, `screenshot`) are serialized with an `asyncio.Lock` (with a 10s acquire timeout) so concurrent MCP clients cannot interleave keystrokes or contend for capture resources. Read-only `subscribe`-cached queries (`get_state`, `get_audio_status`, `get_foreground_app`) bypass the UI lock and, when the TV is in standby, return `connected: false, stale: true` alongside `last_known` values rather than failing.

### Inventive Contradictions & Resolutions

1. **Fail-Fast Standby (`1008`) vs. Detecting Manual Power-Ons:**
   Mutating tools need `1008` to fail immediately, while background reconnect needs to notice when someone turns the TV on with the physical remote without hammering `wss://:3001` every second.
   - *Resolution:* Combine asymmetric reconnect backoff (e.g., 2s -> 5s -> 15s -> 30s) with passive SSDP `ssdp:alive` multicast listening (`239.255.255.250:1900`), which wakes the reconnect loop immediately when the TV boots, while `tv_wake` triggers fast polling (~1s) after sending the Wake-on-LAN packet to `255.255.255.255:9`.

2. **Stale-Capture Risk vs. `/tmp` RAM Budget on the TV:**
   Overwriting a single fixed `/tmp/test.jpg` risks returning a stale previous screenshot if `executeOneShot` fails silently, while leaving unique capture files behind risks filling `/tmp` on the TV's ~1.2 GB RAM.
   - *Resolution:* Capture to a unique per-call path (`/tmp/lg_smtv_cap_<seq>.jpg`), only pull via SFTP if `executeOneShot` returns `returnValue: true`, read bytes directly into memory (`await f.read()`), and always `await sftp.remove(remote_path)` in a `finally:` block.

3. **Pure LLM-Free Server vs. Vision-Loop Turn Overhead:**
   In `docs/poc/crunchyroll-e2e-experiment.md`, Step 6 (`ime/insertText` before the native keyboard opened) returned the exact same `18,947 B` screenshot and cost an extra ~11–13s vision/LLM turn.
   - *Resolution:* Include deterministic `frame_sha256` and `changed_since_last: bool` metadata on all captures so the host's decision loop can skip redundant vision-model calls when a screen has not changed.

### Tool Ergonomics, Safety & Environment

- **Partial-Success Semantics:** On composite actions (`keys(..., capture=True)`), if keystrokes succeed and Dev Mode SSH fails, return partial success (`keys_sent: true`, `capture_error: "..."`) so the caller never double-sends keystrokes.
- **Native Keyboard Quirk in `type_text`:** `ssap://com.webos.service.ime/insertText` returns `returnValue: true` silently even when the native webOS keyboard is not open. Expose `type_text(text, send_enter=True, press_enter_first=False, capture=False)` and state this requirement explicitly in the tool description.
- **Verified `asyncssh` Transport & Headless Daemon Keying:** Dev Mode SSH on port `9922` (user `prisoner`) is empirically verified on Python 3.14 with `asyncssh 2.24.0` using `config=[]`, `server_host_key_algs=["ssh-rsa", ...]`, and `signature_algs=["ssh-rsa", ...]`. Because a 24/7 systemd daemon on `server-local` has no interactive TTY or unlocked `ssh-agent`, `asyncssh.connect` must support a dedicated passwordless service key or an optional `WEBOS_SSH_PASSPHRASE` env/secret.
- **WoL Broadcast Targeting:** Because `server-local` (`192.168.1.36`) is multi-homed (LAN Ethernet + Tailscale), make the WoL broadcast address configurable (e.g., subnet broadcast `192.168.1.255` or `255.255.255.255`, port `9`) so magic packets exit on the LAN interface toward the TV.
- **Guardrails & HTTP Security:** Default `max_volume = 40` (configurable via env/config); keep `turn_off` and `switch_input` explicit; omit `raw_ssap` by default (gated behind opt-in `ENABLE_RAW_SSAP=true`). Bind `streamable-http` only to the homelab LAN/Tailscale interface, validate `Authorization: Bearer <token>` with `hmac.compare_digest`, and configure MCP `TransportSecuritySettings`.
- **Offline Testability:** Abstract SSAP, pointer socket, WoL, and `asyncssh`/SFTP behind thin Python `Protocol` interfaces so `pytest` runs full offline contract tests without hitting the live TV.

### Ideal Final Result

A single homelab `streamable-http` process keeps all TV transports warm with zero outbound LLM dependencies, fails fast on standby (`1008`), degrades cleanly when Dev Mode SSH expires, cleans up every per-call screenshot in memory, and returns compact state/image payloads with deterministic change detection so any external host or decision model can drive the TV in seconds.

## Goals vs. Non-Goals

* **In-Scope Goals:**
  * Long-lived `streamable-http` MCP daemon (LAN/Tailscale bound, static Bearer token auth) plus `stdio` transport for local testing.
  * Unified `tv_core` async module with central SSAP dispatcher, pointer socket auto-recovery, `subscribe` state caching, and `asyncio.Lock` serialization for UI/capture tools.
  * Explicit `tv_wake` tool (WoL to `255.255.255.255:9` + polling) and fail-fast `TV_OFFLINE_STANDBY` handling on WebSocket close code `1008` for mutating tools.
  * Read-only state queries returning `connected: false, stale: true` with `last_known` values when offline.
  * Composite, decision-model-friendly tools (`keys` with delays + optional inline `ImageContent` and partial-success reporting, `type_text`, `launch` with deep-link helpers, `screenshot` with `frame_sha256` and `changed_since_last`, `set_volume` with configurable `max_volume=40` cap, explicit `turn_off` / `switch_input`, and opt-in `ENABLE_RAW_SSAP=true`).
  * One-time CLI `pair` command for interactive on-screen `PROMPT` registration and `client-key` persistence.
  * Persistent `asyncssh`/SFTP (`:9922`) capture to `/tmp/lg_smtv_cap_<seq>.jpg` with `finally: sftp.remove(...)` cleanup and graceful degradation when Dev Mode is off or expired.
  * Offline `pytest` unit/contract test suite mocking SSAP, pointer, WoL, and `asyncssh` boundaries, passing `pyright` and `ruff`.

* **Out-of-Scope Non-Goals:**
  * Outbound LLM/VLM API calls, API keys, or built-in vision-navigation loops inside `lg-smtv-mcp`.
  * Extra HTTP or IPC wrapper servers behind the MCP server.
  * Arbitrary SSH command execution on the TV or retaining screenshot files on `/tmp`.
  * Multi-TV fleet management or automatic first-time `PROMPT` pairing inside MCP tool calls.
  * Implicit auto-wake on normal control/query tools.

## Alternatives Weighed

* **Single-process `stdio` per client vs. `streamable-http` daemon:** Rejected `stdio`-only because per-session spawns repeat TLS/SSAP/SSH handshakes and allow concurrent clients to contend for the TV's single pointer socket. `streamable-http` is primary; `stdio` remains supported for local test harnesses.
* **In-server vision sub-loop vs. pure device MCP:** Rejected in-server vision loop because it couples a hardware interface to model API keys, providers, and prompts. Kept orchestration in the host and moved efficiency gains into composite tools and deterministic `frame_sha256` / `changed_since_last` metadata.
* **OpenSSH `ControlMaster` subprocess vs. `asyncssh` persistent SFTP:** Rejected OpenSSH `ControlMaster` subprocess in favor of `asyncssh` (`2.24.0`), which was empirically verified on Python 3.14 against `192.168.1.11:9922` and avoids subprocess management while keeping `Protocol` mocking clean.
* **Fixed `/tmp/test.jpg` overwrite vs. per-call `/tmp/lg_smtv_cap_<seq>.jpg` + `finally: remove`:** Rejected fixed path because a failed `executeOneShot` could return a stale prior image; per-call paths pulled into memory and deleted in `finally:` prevent both stale reads and `/tmp` exhaustion.
* **Unconditional `raw_ssap` vs. gated `ENABLE_RAW_SSAP=true`:** Omitted `raw_ssap` from the default tool surface to keep decision-model schemas small, while allowing opt-in debug access via config.

## Key Decisions & Staging for Next Steps

* **Transport & Auth:** `streamable-http` daemon (LAN/Tailscale bind, `Authorization: Bearer <token>` via `hmac.compare_digest`, `TransportSecuritySettings`) + `stdio` for local harnesses.
* **SSAP & Concurrency:** Background reader task routing by `msg["id"]` + `subscribe` cache; `asyncio.Lock` (10s timeout) on UI/capture tools; lock-free read queries returning `connected: false, stale: true` + `last_known` when offline.
* **Standby & Wake:** Fail fast on `1008` with `TV_OFFLINE_STANDBY`; explicit `tv_wake` via WoL (`255.255.255.255:9`); asymmetric reconnect backoff + optional passive SSDP `ssdp:alive` listener.
* **Capture & Guardrails:** `asyncssh` persistent SFTP (`config=[]`, explicit `ssh-rsa` algs), `/tmp/lg_smtv_cap_<seq>.jpg` + `finally: sftp.remove`, `frame_sha256` + `changed_since_last`, partial-success reporting on `keys(..., capture=True)`, `max_volume = 40` default, `ENABLE_RAW_SSAP=true` opt-in.
* **Testing:** Offline `pytest` contract tests over `Protocol` boundaries + `pyright` + `ruff` + manual live-TV checks.
