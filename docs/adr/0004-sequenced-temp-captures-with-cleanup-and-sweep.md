# ADR-0004: Sequenced Temp Captures with Cleanup and Sweep

- Status: Accepted (Implementation planned in M2 `#12`)
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

## Related Decisions
- Documented in [docs/designs/lg-smtv-mcp.md](../designs/lg-smtv-mcp.md) (Alternative D) and [ADR-0003](0003-asyncssh-protocol-boundary-for-dev-mode-capture.md).
