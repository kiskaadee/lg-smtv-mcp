# Crunchyroll E2E Comparative Report (PoC vs. `lg-smtv-mcp`)

Comparison between the initial PoC run ([`crunchyroll-e2e-experiment.md`](../poc/crunchyroll-e2e-experiment.md)) and the first live `lg-smtv-mcp` (`main` @ `aac0035`) subagent run (`b050beec-fc4c-4240-a54c-4613b8364893`) against `65UR8750PSA` (`WebOS/4.1.0`).

**Goal:** Launch Crunchyroll, select profile (`Andres`), open *Attack on Titan* (*Ataque a los Titanes*), and start Episode 1.

---

## Executive Summary

| Metric | PoC Baseline | `lg-smtv-mcp` (`aac0035`) | Notes |
| :--- | :--- | :--- | :--- |
| **Total Elapsed Time** | `7m 07s` (`427s`) | `4m 56s` (`296s`) | `lg-smtv-mcp` run also included `tv_wake` (`5.57s`), `T17 -> T1` (`UP x16`), and HUD verification |
| **Timed TV Control Span** | `~7m 05s` | `4m 38s` (`09:37:29 -> 09:42:07`) | Excludes initial schema `read_file` setup |
| **Steps / Captures** | `13` steps / `12` captures | `21` steps / `15` captures | Different reasoning paths; not 1-to-1 comparable |
| **Per-Step Tool Duration** | `~8–10s` (fresh `nix develop` + TLS + `register` + `scp`) | `2–5s` (persistent SSAP + `asyncssh` SFTP) | Transport overhead eliminated by `TvManager` |
| **Capture Size Range** | `9,606 B` – `101,992 B` | `8,787 B` – `103,192 B` | 11/15 captures exceeded ~10–15 KB and offloaded to disk |

### What MCP Bought vs. What It Didn't

- **What `TvManager` (not MCP itself) bought:** Persistent SSAP/pointer sockets and warm `asyncssh` SFTP cut per-step transport latency from `~8–10s` to `~2–5s`. A persistent REST API backed by `TvManager` would yield the same transport speedup.
- **What MCP bought:** Zero-glue host integration (typed schemas, `ImageContent` multimodal blocks, and `stdio` / `streamable-http` lifecycle) across Antigravity and other MCP clients without writing custom client wrappers.
- **What MCP did *not* buy:** Fewer UI discovery steps or faster LLM turns. Because 11 of 15 captures (`11 KB – 103 KB`) exceeded Antigravity's inline image threshold, Antigravity offloaded them to disk and spent an extra `read_file` LLM turn (`~10–15s` gap between steps) on almost every UI screen.

---

## Three-Bucket Latency Breakdown (`lg-smtv-mcp` Run)

1. **Transport & Process Overhead (`~45–60s` total across 21 steps):**
   Persistent SSAP + `asyncssh` SFTP took `2–5s` per capture call (including `settle_ms`).
2. **Host / LLM Turn Overhead (`~3m 30s`):**
   - Full coding-agent turn latency (`~8–12s` per turn).
   - **11 extra `read_file` turns** caused by disk-offloaded `ImageContent` (`> ~10–15 KB`), directly motivating Issue **#20** (host-side JPEG downscaling/recompression).
   - **Retry / loading turns:** Skeleton/loading captures (Steps 5, 7, 13, 18, 19) and `type_text` without native IME (Steps 10–11), motivating Issue **#23** (`wait_for_change`).
3. **TV / App Hardware Settling (`~20s`):**
   `tv_wake` (`5.57s`), Crunchyroll splash/profile load, `UP x16` list scroll, and video buffering.

---

## Detailed Step Log (`lg-smtv-mcp` @ `aac0035`)

| Step | Time (`-05:00`) | Action / Tool + Arguments | Observed Screen / Result | Capture Size | `changed_since_last` / Hash |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 0 | `09:37:17 -> 09:37:25` | `date` + `read_file` on 7 `lg-smtv` schemas | Start `09:37:19.214`; schemas loaded | — | — |
| 1 | `09:37:29 -> 09:37:30` | `get_state({})` | `connected: false, stale: true` (TV in standby) | — | — |
| 2 | `09:37:34 -> 09:37:42` | `tv_wake({})` | `status: "connected"`, `elapsed_ms: 5572` | — | — |
| 3 | `09:37:45 -> 09:37:48` | `list_apps({})` | Found `com.crunchyroll.webapp` | — | — |
| 4 | `09:37:53 -> 09:37:57` | `launch({"app_id": "com.crunchyroll.webapp"})` | `returnValue: true` | — | — |
| 5 | `09:38:01 -> 09:38:05` | `screenshot({})` + `read_file` | Crunchyroll splash screen | `94,613 B` | `true` / `548af733` |
| 6 | `09:38:16 -> 09:38:19` | `screenshot({})` + `read_file` | Profile picker (`"¿Quién está viendo esto?"`, `Andres` focused) | `65,872 B` | `true` / `4cf77ce7` |
| 7 | `09:38:31 -> 09:38:36` | `keys(["ENTER"], settle_ms=2000, capture=True)` + `read_file` | Profile selected; home sidebar rendered, content loading | `11,050 B` | `true` / `7f544a6e` |
| 8 | `09:38:48 -> 09:38:50` | `screenshot({})` + `read_file` | Home screen loaded; focus on `"Comenzar a Ver"` | `92,524 B` | `true` / `08d99efe` |
| 9 | `09:39:00 -> 09:39:04` | `keys(["LEFT", "UP", "ENTER"], delay_ms=250, settle_ms=800, capture=True)` + `read_file` | Search opened; `"Ataque a los Titanes"` in recent searches | `22,346 B` | `true` / `ac885345` |
| 10 | `09:39:15 -> 09:39:17` | `keys(["ENTER"], settle_ms=800, capture=True)` | No screen change (Crunchyroll search input did not open native IME) | `22,346 B` | `false` / `ac885345` |
| 11 | `09:39:23 -> 09:39:26` | `type_text("Attack on Titan", send_enter=True, capture=True)` | `text_sent: true`, screen unchanged (IME not open) | `22,346 B` | `false` / `ac885345` |
| 12 | `09:39:32 -> 09:39:35` | `keys(["DOWN"], settle_ms=800, capture=True)` + `read_file` | Focused `"Ataque a los Titanes"` under `"Búsquedas recientes"` | `22,824 B` | `true` / `bce1361e` |
| 13 | `09:39:49 -> 09:39:53` | `keys(["ENTER"], settle_ms=1500, capture=True)` + `read_file` | Series page loading (skeleton placeholders) | `16,665 B` | `true` / `463499ed` |
| 14 | `09:40:05 -> 09:40:08` | `screenshot({})` + `read_file` | Series page loaded (`"Continuar E17"`) | `89,709 B` | `true` / `bc2357f7` |
| 15 | `09:40:20 -> 09:40:23` | `keys(["DOWN"], settle_ms=800, capture=True)` + `read_file` | Focused `"EPISODIOS"` tab; Season 1 listed with `T17` | `96,025 B` | `true` / `e91c7353` |
| 16 | `09:40:38 -> 09:40:41` | `keys(["DOWN", "RIGHT"], delay_ms=250, settle_ms=800, capture=True)` + `read_file` | Focused `T17 - La titán hembra...` | `102,035 B` | `true` / `59a5942b` |
| 17 | `09:40:56 -> 09:41:01` | `keys(["UP"] * 16, delay_ms=150, settle_ms=800, capture=True)` + `read_file` | Focused `T1 - Para ti, en 2000 años -- La caída de S...` | `103,192 B` | `true` / `1f611a48` |
| 18 | `09:41:28 -> 09:41:37` | `keys(["ENTER"], settle_ms=2000, capture=True)` *(after `settle_ms=2500` failed `INVALID_ARGUMENT`)* | Player buffering (orange spinner) | `9,608 B` | `true` / `1cf098ba` |
| 19 | `09:41:45 -> 09:41:48` | `screenshot({})` | Still buffering (spinner rotated) | `9,565 B` | `true` / `66e7dae3` |
| 20 | `09:41:55 -> 09:41:57` | `screenshot({})` | Spinner gone, black frame (DRM video layer) | `8,787 B` | `true` / `f6ed2d39` |
| 21 | `09:42:05 -> 09:42:07` | `keys(["DOWN"], settle_ms=500, capture=True)` + `read_file` (`09:42:15`) | Player HUD: `E1 - Para ti, en 2000 años -- La caída de Shiganshina, parte 1` at `00:19 / 25:20` | `17,889 B` | `true` / `02654704` |
