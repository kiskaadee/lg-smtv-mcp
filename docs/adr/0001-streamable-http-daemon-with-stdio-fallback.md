# ADR-0001: Streamable HTTP Daemon with Stdio Fallback

- Status: Accepted
- Date: 2026-11-15
- Deciders: kiskaadee

## Context and Problem Statement
One-shot CLI scripts against an LG webOS TV incur ~8–10s per step in repeated `wss://:3001` TLS + `register` handshakes, `getPointerInputSocket` acquisition, and SSH setup, and cannot maintain `subscribe` state streams across tool calls.

## Decision Outcome
Chosen option: "Run `lg-smtv-mcp` primarily as a persistent `streamable-http` ASGI daemon (with `stdio` supported for local test harnesses)", because a long-lived daemon keeps SSAP, `subscribe` state, and pointer/capture sessions warm while `BearerAuthMiddleware` and `TransportSecuritySettings` secure LAN access.

## Consequences
- Positive: Eliminates per-step TLS/SSAP handshake overhead, preserves `subscribe` cache (`volume`, `muted`, `sound_output`, `foreground_app`), and serializes `ui_lock` across concurrent calls.
- Negative: Requires `MCP_BEARER_TOKEN` configuration and daemon lifecycle management (`serve` / `SIGHUP`) when deployed over HTTP.

## Alternatives Considered
- Single-process `stdio` per client only: Rejected because each client session spawns a fresh process, repeating handshakes and contending for the TV's single pointer socket.

## Related Decisions
- Documented in [docs/designs/lg-smtv-mcp.md](../designs/lg-smtv-mcp.md) (Alternative A).
