# Roadmap

## Current Direction

`lg-smtv-mcp` has completed its M1 MVP control plane (persistent SSAP, pointer input, WoL recovery, FastMCP tools, Bearer auth, `pair`/`serve` CLI, and full-stack offline E2E verification) and the M2 capture and E2E-hardening slices. Remaining M2 work is deployment packaging and its `systemd` hardening follow-up.

## Near Term (Milestone M2)

- **Systemd Unit & Deployment Docs ([#13](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/issues/13)):** Add `deploy/lg-smtv-mcp.service` template and document systemd + Developer Mode SSH setup in `README.md`.
- **Systemd Unit Sandboxing Hardening ([#17](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/issues/17)):** Follow-up to `#13`, tightening the unit's sandboxing directives once the baseline deployment template lands.

## Medium Term

- **Live-TV Operator Smoke Verification:** Execute the manual LAN smoke checklist in [`README.md`](README.md#operator-live-tv-e2e-smoke-test-checklist) and Developer Mode capture verification against the target `WebOS/4.1.0` TV.

## Completed

### M2: Capture & E2E Hardening (partial, 2026-11-16)
- **#11 (PR [#14](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/14)):** E2E harness hardening in [`tests/test_e2e_mvp.py`](tests/test_e2e_mvp.py) — fast-fail ASGI lifespan startup, `elapsed_ms` contract check, and post-`turn_off()` `1008` -> `get_state` stale parity.
- **#12 (PR [#15](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/15)):** `tv_core/capture.py` (`CaptureClient` + `AsyncSshCaptureTransport`), default `server.py` wiring, and offline capture contract tests; review remediation CR-01..CR-11 landed in `61d2315`.

### M1: MVP Core & End-to-End Verification (Completed 2026-11-16)
- **#1 (PR [#6](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/6)):** Nix flake packaging, `pyproject.toml`, Pydantic v2 `TvConfig`, and `ConfigError` / `TvError` taxonomy.
- **#2 (PR [#7](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/7)):** `tv_core` (`SsapClient` multiplexer, `PointerClient`, `TvManager` state machine, `FrameCapturer` seam).
- **#3 (PR [#9](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/9)):** `MCPServer` tools, `BearerAuthMiddleware` + `TransportSecuritySettings`, ASGI `lifespan`, and `pair` / `serve` CLI.
- **#4 (PR [#10](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/10)):** Full-stack offline MVP E2E test (`tests/test_e2e_mvp.py`) and live-TV operator smoke checklist (`README.md`).
