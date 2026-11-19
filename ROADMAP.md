# Roadmap

## Current Direction

`lg-smtv-mcp` has completed Milestone M2 (capture, E2E hardening, and systemd deployment packaging) and the post-M2 `systemd` unit sandboxing hardening follow-up ([#17](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/issues/17), PR [#18](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/18)). Active work is live-TV operator smoke verification (and downstream Canopy deployment).

## Near Term

- **Live-TV Operator Smoke Verification:** Execute the manual LAN smoke checklist in [`README.md`](README.md#operator-live-tv-e2e-smoke-test-checklist) and Developer Mode capture verification against the target `WebOS/4.1.0` TV.

## Completed

### Post-M2 Hardening (Completed 2026-11-18)
- **#17 (PR [#18](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/18)):** Hardened [`deploy/lg-smtv-mcp.service`](deploy/lg-smtv-mcp.service) with bounded restart throttling (`StartLimitIntervalSec=60s`, `StartLimitBurst=5`) and a 14-directive sandboxing baseline (`9.0 UNSAFE` → `4.9 OK`), added automated `systemd-analyze` tests in [`tests/test_deploy.py`](tests/test_deploy.py), documented adopted/rejected directives in [`README.md`](README.md#sandboxing--security-profile), and fixed `asyncssh` SFTP capture reads (`sftp.open()`) in [`src/lg_smtv_mcp/tv_core/capture.py`](src/lg_smtv_mcp/tv_core/capture.py).

### M2: Capture, E2E Hardening & Systemd Deployment (Completed 2026-11-17)
- **#11 (PR [#14](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/14)):** E2E harness hardening in [`tests/test_e2e_mvp.py`](tests/test_e2e_mvp.py) — fast-fail ASGI lifespan startup, `elapsed_ms` contract check, and post-`turn_off()` `1008` -> `get_state` stale parity.
- **#12 (PR [#15](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/15)):** `tv_core/capture.py` (`CaptureClient` + `AsyncSshCaptureTransport`), default `server.py` wiring, and offline capture contract tests; review remediation CR-01..CR-11 landed in `61d2315`.
- **#13 (PR [#16](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/16)):** `deploy/lg-smtv-mcp.service` systemd template, deployment + Developer Mode SSH setup docs in `README.md`, and DNS-rebinding / PATH env guidance.

### M1: MVP Core & End-to-End Verification (Completed 2026-11-16)
- **#1 (PR [#6](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/6)):** Nix flake packaging, `pyproject.toml`, Pydantic v2 `TvConfig`, and `ConfigError` / `TvError` taxonomy.
- **#2 (PR [#7](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/7)):** `tv_core` (`SsapClient` multiplexer, `PointerClient`, `TvManager` state machine, `FrameCapturer` seam).
- **#3 (PR [#9](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/9)):** `MCPServer` tools, `BearerAuthMiddleware` + `TransportSecuritySettings`, ASGI `lifespan`, and `pair` / `serve` CLI.
- **#4 (PR [#10](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/10)):** Full-stack offline MVP E2E test (`tests/test_e2e_mvp.py`) and live-TV operator smoke checklist (`README.md`).
