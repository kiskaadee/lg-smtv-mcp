# ADR-0002: LLM-Free MCP Server with Composite Tools

- Status: Accepted
- Date: 2026-11-15
- Deciders: kiskaadee

## Context and Problem Statement
Vision-guided TV UI navigation requires multi-step button presses and screen inspection, which could either be orchestrated inside the MCP server via an embedded VLM loop or delegated to the calling MCP host.

## Decision Outcome
Chosen option: "Keep `lg-smtv-mcp` strictly LLM-free and expose decision-model-friendly composite tools (`keys(..., capture=True)`, `type_text(..., capture=True)`, `frame_sha256` / `changed_since_last`)", because it decouples a local hardware driver from external LLM/VLM SDKs, API keys, and prompt logic while still minimizing round-trips.

## Consequences
- Positive: Zero model dependencies or outbound API keys in the daemon; deterministic tool behavior; host can skip redundant vision turns when `changed_since_last` is `false`.
- Negative: Multi-step navigation loops must be driven by the MCP host/agent rather than a single natural-language tool call.

## Alternatives Considered
- In-server vision navigation sub-loop: Rejected because it couples the hardware control plane to external LLM/VLM providers, model selection, and prompt engineering.

## Related Decisions
- Documented in [docs/designs/lg-smtv-mcp.md](../designs/lg-smtv-mcp.md) (Alternative B).
