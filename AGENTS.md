# Project Guidelines & Safety Rules for AI Agents

## 1. Mission & Authority

`lg-smtv-mcp` is a Python Model Context Protocol (MCP) server for controlling a single pre-paired LG webOS Smart TV over the local network. It runs primarily as a long-lived `streamable-http` daemon on a homelab server on the same LAN (with `stdio` supported for local testing), keeping SSAP WebSocket, pointer input socket, `subscribe` state cache, and optional Developer Mode `asyncssh`/SFTP connections warm. It stays strictly LLM-free: vision-guided UI navigation lives in the MCP host (such as Antigravity, Claude, or a `gemini-decision-1` loop), while the server exposes high-level, decision-model-friendly tools and composite actions (such as batched key sequences with optional inline `ImageContent` screenshots).

---

## 2. Working Tree Authority & Safety (MANDATORY)

The working tree represents the user's active work.
1. **Inspect First:** Run `git status` and list/read target directories/files before creating or modifying anything.
2. **Check for Existing Files:** Before creating any new file, verify whether the file (or a user equivalent) already exists on disk. Never overwrite or re-scaffold user work.
3. **Never Overwrite Blindly:** Use available file-reading tools. Preserve user comments, links, and conventions. Prefer surgical edits over wholesale overwrites.
4. **No Destructive Git Commands:** Never run `reset --hard`, `clean -fd`, `restore`, `checkout`, `stash`, `rebase`, or `amend` without explicit permission.
5. **Incremental Retrospective Commits:** Commit each coherent, verified micro-step on a working branch rather than accumulating an entire feature into one large commit. Commits on `main` are allowed for documentation changes (not affecting functionality or architecture). Multi-file commits must include structured `-` bullets explaining *what* changed and *why* per area so the Git history tells a retrospective of the design decisions.
6. **Live Planning & Task Synchronization:** Authoritative tracker is `kiskaadee/lg-smtv-mcp` on `gitea.roadtotech.me` (Project Board ID `6`: `Project Pipeline`), synchronized against `docs/designs/lg-smtv-mcp.md`. Keep Gitea issues, milestones, and board cards aligned with code reality as slices complete; re-run `gitea-plan` on scope shifts or when reaching a milestone Readiness Gate (such as decomposing `M2` after the `M1` MVP E2E gate).
7. **Final Diff Verification:** Before completing work, run `git status` and `git diff` to verify that no unrelated user changes were modified or discarded. Distinguish user changes from agent changes in completion reports.

---

## 3. Architecture & Component Contracts (Progressive Triggers)

- **Mandatory Architecture Review:** Always read `docs/designs/lg-smtv-mcp.md` and `docs/poc/capabilities.md` (and `docs/architecture.md` once created) before changing MCP tool schemas, SSAP/SSH transport boundaries, or directory layouts.
- **Stop and Ask:** If an implementation conflicts with documentation or an ambiguous requirement materially affects architecture, stop and ask the user rather than guessing or silently changing the design.
- **No Speculative Robustness:** Do not add unrequested fallback layers, complex generic abstractions, or future-proofing bloat.

### 3.1 Documentation & Diagram Standards
- **Contracts Without Code Duplication:** Avoid pasting Python class or dataclass blocks into architecture/design docs. Model relationships with a Mermaid `classDiagram` (or `erDiagram`), summarize exact signatures in a concise table, and link directly to the authoritative source file (label as *(planned)* if not yet created).
- **Policy vs. Mechanism in Diagrams:** Keep flowcharts at the domain-policy level (e.g., *"Is the TV in standby (`1008`)?"*) rather than mirroring low-level library syntax. Capture mandatory mechanism invariants in prose or the detailed implementation manifest.

---

## 4. Operational & Protocol Invariants

- **No Personal Setup Leakage:** Never hardcode personal LAN IPs, MAC addresses, SSH host aliases, or local filesystem paths in source code, default configs, tests, or new docs. Treat device coordinates as runtime config (`.env` / env vars / CLI args) and use placeholders (`<TV_IP>`, `<TV_MAC>`) in examples.
- **Live-Hardware Safety & Offline Tests:** Never send commands to a physical TV during automated runs or tests without explicit user approval. All automated tests must run offline against mocked SSAP, pointer socket, WoL, and `asyncssh` `Protocol` boundaries.
- **Standby & Fail-Fast:** WebSocket close code `1008` (`Try Again Later (EWS)`) means `TV_OFFLINE_STANDBY`—mutating tools must fail fast and never implicitly auto-wake the TV; read-only state queries return `connected: false, stale: true` with `last_known` values.
- **SSAP & Pointer Socket:** Always include `manifest.permissions` in `register` even when reusing a saved `client-key`. Route frames by `msg["id"]` in a single background reader task; never call ad-hoc `ws.recv()` alongside `subscribe`.
- **Dev Mode SFTP Capture:** `asyncssh` must use `config=[]` and explicit `"ssh-rsa"` algorithms (no `+ssh-rsa` prefix). Capture to `/tmp/lg_smtv_cap_<seq>.jpg`, pull into memory only on `returnValue: true`, and `sftp.remove()` in `finally:`. On composite `keys(..., capture=True)`, report partial success (`keys_sent: true, capture_error: ...`) if SSH fails after keystrokes succeed.
- **PoC Archive & Secrets:** `docs/poc/` is read-only historical reference—do not modify PoC scripts for new features. Never commit `.client_key.json`, `.env`, SSH keys, or `*.jpg` captures.

---

## 5. Verification & Completion

Every task must be verified inside `nix develop` before declaring completion:
1. Run linters, formatters, and type checks: `nix develop --command ruff check .` and `nix develop --command pyright`.
2. Run test suites when tests are present: `nix develop --command pytest`.
3. Report: (1) What changed, (2) Files modified/created, (3) Verification performed, (4) Unverified areas/risks.
