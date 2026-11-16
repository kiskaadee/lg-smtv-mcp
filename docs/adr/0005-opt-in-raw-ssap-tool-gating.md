# ADR-0005: Opt-In Raw SSAP Tool Gating

- Status: Accepted
- Date: 2026-11-15
- Deciders: kiskaadee

## Context and Problem Statement
Exposing an unrestricted SSAP pass-through tool allows calling arbitrary TV URIs, which expands the MCP tool schema and can bypass safety guardrails such as `MAX_VOLUME` and explicit `turn_off`.

## Decision Outcome
Chosen option: "Omit `raw_ssap` by default and register it only when `ENABLE_RAW_SSAP=true` is explicitly configured", because closed-set decision models stay within safe, bounded tools in normal operation while operators retain an escape hatch for debugging.

## Consequences
- Positive: Preserves `MAX_VOLUME` and input/power guardrails by default and keeps the MCP tool schema compact.
- Negative: Ad-hoc SSAP experiments require restarting the server with `ENABLE_RAW_SSAP=true`.

## Alternatives Considered
- Unconditional `raw_ssap` tool: Rejected because it weakens guardrails and enlarges the tool surface for decision-model callers.

## Related Decisions
- Documented in [docs/designs/lg-smtv-mcp.md](../designs/lg-smtv-mcp.md) (Alternative E).
