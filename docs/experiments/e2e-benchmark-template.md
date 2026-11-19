# Standardized E2E Benchmark Template (`lg-smtv-mcp`)

Use this template to benchmark changes (such as **#20** JPEG downscaling or **#23** `wait_for_change`) without agent reasoning variance skewing the comparison.

---

## 1. Preconditions (Pin Before Starting)

- **Commit / Branch:** `<git rev-parse --short HEAD>` (`<branch>`)
- **Server Config:** `CAPTURE_MAX_WIDTH=<value|unset>`, `CAPTURE_JPEG_QUALITY=<value|unset>`
- **TV Starting State:** Powered ON at Live TV (or Standby — record which), Crunchyroll closed.
- **Timer Boundary:** Start `T=0` at the first TV-control tool call (`launch` or `tv_wake`), **excluding** one-time MCP schema `read_file` setup. End `T_end` when the playback HUD is verified.

---

## 2. Standardized Subagent Prompt

```text
Goal: Launch Crunchyroll, select profile "Andres", open Attack on Titan ("Ataque a los Titanes"), and start Episode 1.

Execution Rules (fixed policy for benchmark parity):
1. Load any needed lg-smtv schemas first, then record `T0` (`date +"%H:%M:%S.%3N"`).
2. Always prefer `keys(..., capture=True)` over separate `keys` + `screenshot` calls.
3. Keep `settle_ms <= 2000` (use `1500–2000` on app launch, profile select, and series page open).
4. On Crunchyroll Search, select "Ataque a los Titanes" from Recent Searches if visible instead of calling `type_text`.
5. Once playback starts (black DRM frame), press `DOWN` once with `capture=True` to verify Episode 1 on the player HUD.
6. Do NOT write or edit any repository files.

For every step, record:
- Step #, T+elapsed (s), Tool + arguments, Tool duration (ms/s), Offloaded to disk? (Y/N), Capture size_bytes, changed_since_last, Observed screen.
```

---

## 3. Normalized Scorecard

| Metric | Baseline (`aac0035`) | Candidate (`<sha>`) | Delta |
| :--- | :--- | :--- | :--- |
| **Total Timed Span (`T0 -> T_end`)** | `4m 38s` (`278s`) | | |
| **Total Steps / Captures** | `21` / `15` | | |
| **Median Capture Size (B)** | `22,824 B` | | |
| **Offloaded Captures (`read_file` turns)** | `11` / `15` | | |
| **Wasted / Retry Steps** | `4` (steps 10, 11, 13→14, 18→19) | | |

---

## 4. Step-by-Step Telemetry Table

| Step | `T+s` | Tool + Arguments | Tool Duration | Offloaded? (`Y/N`) | `size_bytes` | `changed_since_last` | Observed Screen / Result |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | | | | | | | |
