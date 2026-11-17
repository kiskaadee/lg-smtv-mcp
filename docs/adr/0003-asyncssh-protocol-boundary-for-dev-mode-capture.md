# ADR-0003: Asyncssh Protocol Boundary for Dev Mode Capture

- Status: Accepted (implemented in M2 `#12` / PR [#15](https://gitea.roadtotech.me/kiskaadee/lg-smtv-mcp/pulls/15))
- Date: 2026-11-15
- Deciders: kiskaadee

## Context and Problem Statement
Retail LG webOS TVs do not serve `/tmp` screenshots over HTTPS (`:3001`), requiring SFTP retrieval from the Developer Mode SSH server (`prisoner@<TV_IP>:9922`) using legacy `ssh-rsa` algorithms.

## Decision Outcome
Chosen option: "Use `asyncssh` (`config=[]`, explicit `ssh-rsa` host-key and signature algorithms) wrapped behind `CaptureTransport` / `FrameCapturer` `Protocol` interfaces", because it keeps SFTP sessions persistent in-process and enables deterministic offline contract testing via protocol doubles.

## Consequences
- Positive: Eliminates per-capture SSH subprocess startup and `ControlMaster` socket management; cleanly mockable in `pytest`; degrades gracefully to `SCREENSHOT_UNAVAILABLE` when Developer Mode expires.
- Negative: Requires explicit legacy `ssh-rsa` algorithm configuration in `asyncssh` and `known_hosts=None` default on LAN (overridable via `SSH_KNOWN_HOSTS_PATH`).

## Alternatives Considered
- OpenSSH `ControlMaster` subprocesses: Rejected because managing background control sockets, parsing subprocess output/exit codes, and writing offline unit tests is more brittle.

## Related Decisions
- Documented in [docs/designs/lg-smtv-mcp.md](../designs/lg-smtv-mcp.md) (Alternative C) and [ADR-0004](0004-sequenced-temp-captures-with-cleanup-and-sweep.md).
