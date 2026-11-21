# Roadmap

## Current Direction

`lg-smtv-mcp` has completed Milestone M2 (capture, E2E hardening, and systemd deployment packaging) and `#17` (`systemd` sandboxing hardening). Active work is **Milestone M2.1: Live-TV Polish & Latency Optimizations** (1/4 complete), focused on reducing screenshot payload size/round-trips, fixing immediate `turn_off()` standby transitions, and unblocking `systemd-analyze` checks in the Nix sandbox.

## Near Term

- **#21 (`M2.1`):** `fix(manager): transition to STANDBY_OFFLINE immediately on turn_off()` so immediate `tv_wake()` calls do not return `already_connected` before the TV drops `1008`.
- **#22 (`M2.1`):** `fix(test): unblock systemd-analyze tests inside Nix build sandbox` (investigate `--root=<tmp_path>` or narrow sandbox-only skip while keeping `nix develop` enforcement strict).
- **#23 (`M2.1`):** `feat(capture): add optional wait_for_change settle retry on keys/type_text capture` (500ms bounded retry within the shared 6.0s capture budget when `changed_since_last=false`).

## Completed

### M2.1: Live-TV Polish & Latency Optimizations (In Progress — Completed Items)
- **#20 (PR [#24](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/24), Completed 2026-11-19):** Added optional `CAPTURE_MAX_WIDTH` (`64..3840`) and `CAPTURE_JPEG_QUALITY` (`1..95`) settings in [`config.py`](src/lg_smtv_mcp/config.py), added `pillow` runtime dependency, and implemented host-side JPEG downscaling/recompression in [`capture.py`](src/lg_smtv_mcp/tv_core/capture.py) (reducing median live-TV capture size by 69.1% and disk-only offloads from 60% to 7%).
- **Live-TV E2E Benchmarks (`0d67615`, `84ae8b2`, `128752b`):** Executed standardized Crunchyroll E2E navigation benchmarks against `65UR8750PSA` (`WebOS/4.1.0`) and recorded baseline vs. post-`#20` telemetry in [`docs/experiments/`](docs/experiments/).

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
