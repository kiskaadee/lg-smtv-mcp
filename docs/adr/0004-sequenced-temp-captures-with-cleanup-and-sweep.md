# ADR-0004: Sequenced Temp Captures with Cleanup and Sweep

- Status: Accepted (implemented in M2 `#12` / PR [#15](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/15); amended 2026-11-16)
- Date: 2026-11-15
- Deciders: kiskaadee

## Context and Problem Statement
`ssap://com.webos.service.capture/executeOneShot` writes screenshots to a path on the TV's `/tmp` filesystem (~1.2 GB RAM). Writing to a fixed path risks reading stale frames on silent failure, while uncleaned unique paths can exhaust TV RAM.

## Decision Outcome
Chosen option: "Capture to `/tmp/lg_smtv_cap_<seq>.jpg`, pull bytes into memory only on `returnValue: true`, delete via `finally: sftp.remove()` under a `1–2s` grace period, and sweep `/tmp/lg_smtv_cap_*.jpg` on SFTP connect/reconnect", because it prevents both stale-frame reads and `/tmp` memory exhaustion.

## Consequences
- Positive: Guarantees fresh frame reads per sequence number and bounds `/tmp` usage even when captures fail or time out.
- Negative: Requires a `6s` shared capture deadline plus `1–2s` cleanup grace inside the `25s` `ui_lock` budget.

## Alternatives Considered
- Fixed `/tmp/test.jpg` overwrite path: Rejected because if `executeOneShot` fails silently or races, SFTP pull can read a stale screenshot from an earlier step.

## Amendment (2026-11-16)

PR [#15](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/15) review (CR-01) found that sweeping unconditionally on a new SFTP session deletes the screenshot `executeOneShot` has just written, because the transport connects lazily **after** the capture request. The decision above stands, with one refinement: `sweep_temp_files` accepts `exclude: str | None`, and `_ensure_sftp` passes the in-flight `remote_path`, so a session sweep removes only stale captures left by earlier sessions or crashes. Guarded by `test_async_ssh_capture_transport_sweep_preserves_in_flight_remote_path`.

## Related Decisions
- Documented in [docs/designs/lg-smtv-mcp.md](../designs/lg-smtv-mcp.md) (Alternative D) and [ADR-0003](0003-asyncssh-protocol-boundary-for-dev-mode-capture.md).
