# Roadmap

## Current Direction

`lg-smtv-mcp` has completed its M1 MVP control plane (persistent SSAP, pointer input, WoL recovery, FastMCP tools, Bearer auth, `pair`/`serve` CLI, and full-stack offline E2E verification) and is moving into Milestone M2 to add Developer Mode SFTP screenshot capture, deployment packaging, and E2E test hardening.

## Near Term (Milestone M2)

- **Developer Mode SFTP Capture & Extended Surface ([#5](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/issues/5)):** Implement `tv_core/capture.py` (`CaptureClient` + `AsyncSshCaptureTransport` behind `FrameCapturer` / `CaptureTransport`) with `6s` shared deadline, `finally: sftp.remove()` cleanup, `/tmp/lg_smtv_cap_*.jpg` reconnect sweep, `frame_sha256` / `changed_since_last` tracking, and `deploy/lg-smtv-mcp.service` systemd unit.
- **E2E Harness & Standby Parity Hardening ([#11](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/issues/11)):** Address PR `#10` review follow-ups in [`tests/test_e2e_mvp.py`](tests/test_e2e_mvp.py) (`_build_online_ssap` extraction, fast-fail ASGI lifespan startup errors, `elapsed_ms` contract check, and post-`turn_off()` `1008` -> `get_state` stale verification).

## Medium Term

- **Live-TV Operator Smoke Verification:** Execute the manual LAN smoke checklist in [`README.md`](README.md#operator-live-tv-e2e-smoke-test-checklist) and Developer Mode capture verification against the target `WebOS/4.1.0` TV.

## Completed

### M1: MVP Core & End-to-End Verification (Completed 2026-11-16)
- **#1 (PR [#6](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/6)):** Nix flake packaging, `pyproject.toml`, Pydantic v2 `TvConfig`, and `ConfigError` / `TvError` taxonomy.
- **#2 (PR [#7](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/7)):** `tv_core` (`SsapClient` multiplexer, `PointerClient`, `TvManager` state machine, `FrameCapturer` seam).
- **#3 (PR [#9](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/9)):** `MCPServer` tools, `BearerAuthMiddleware` + `TransportSecuritySettings`, ASGI `lifespan`, and `pair` / `serve` CLI.
- **#4 (PR [#10](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/10)):** Full-stack offline MVP E2E test (`tests/test_e2e_mvp.py`) and live-TV operator smoke checklist (`README.md`).
