# Crunchyroll End-to-End Vision Loop Experiment

Empirical benchmark of a closed-loop `check_screen -> act -> check_screen` workflow against `192.168.1.11` (`65UR8750PSA` / `WebOS/4.1.0`) using one-shot `nix develop` invocations, SSAP WebSockets (`wss://:3001`), the pointer input socket, and `scp` (`:9922`) screenshot pulls.

**Goal:** Launch Crunchyroll, select profile, search for *Attack on Titan*, and start Episode 1.

---

## Timeline (Observed from Transcript)

Total elapsed time from request (`01:07:08-05:00`) to final verification (`01:14:15-05:00`): **7m 07s** (`427s`).

| Step | Local Time (`-05:00`) | Action | Observed Screen / Result | Capture Size |
| :--- | :--- | :--- | :--- | :--- |
| 0 | `01:07:08` | User request | Target: Crunchyroll → *Attack on Titan* Ep 1 | — |
| 1 | `01:07:10` | `listLaunchPoints` | Found `com.crunchyroll.webapp` | — |
| 2 | `01:07:25` | `launch com.crunchyroll.webapp` + `sleep 6` + capture | Crunchyroll splash screen (still loading) | `94,613 B` |
| 3 | `01:07:59` | `sleep 4` + capture | Profile picker (`"¿Quién está viendo esto?"`, `Andres` focused) | `65,872 B` |
| 4 | `01:08:31` | Pointer `ENTER` + `sleep 4` + capture | Crunchyroll Home (`"Nuestras recomendaciones"`) | `27,615 B` |
| 5 | `01:09:06` | Pointer `LEFT -> UP -> ENTER` + capture | Search bar focused (`"Busca una serie..."`) | `18,947 B` |
| 6 | `01:09:38` | Pointer `ENTER` + `ime/insertText` + `sendEnterKey` + capture | **No change** (native keyboard wasn't open yet) | `18,947 B` |
| 7 | `01:10:14` | Pointer `ENTER` + `sleep 1` + capture | Native webOS on-screen keyboard opened | `41,458 B` |
| 8 | `01:10:48` | `ime/insertText("Attack on Titan")` + `sendEnterKey` + capture | Search results loaded (*Attack on Titan: Junior High* #1, *Ataque a los Titanes* #2) | `74,810 B` |
| 9 | `01:11:20` | Pointer `BACK -> DOWN -> DOWN -> RIGHT` + capture | *"Ataque a los Titanes"* highlighted | `65,696 B` |
| 10 | `01:11:55` | Pointer `ENTER` + `sleep 2.5` + capture | Series page (`Continuar E8` focused, episode list loading) | `67,882 B` |
| 11 | `01:12:28` | Pointer `DOWN` + `sleep 1.5` + capture | `EPISODIOS` tab, Season 1 selected, list at `T8` | `97,515 B` |
| 12 | `01:13:01` | Pointer `DOWN -> RIGHT -> UP x7` + capture | `T1 - Para ti, en 2000 años` highlighted | `101,992 B` |
| 13 | `01:13:35` | Pointer `ENTER` + `sleep 2.5` + capture (`01:14:15` read) | Playback buffering spinner over black DRM video plane | `9,606 B` |

---

## Key Technical Takeaways for `lg-smtv-mcp`

1. **`ime/insertText` requires the native keyboard to be open first:**
   In Step 6, `ime/insertText` returned `returnValue: true` even though the text box stayed empty because the native webOS keyboard had not opened yet. Once `ENTER` opened the keyboard (Step 7), `ime/insertText` populated the full string in one call (Step 8).
2. **DRM video renders black in `executeOneShot`:**
   All UI navigation screens captured cleanly at `960x540` (`18 KB`–`102 KB`), while active video playback (Step 13) captured only the UI spinner (`9.6 KB`).
3. **Where the 7 minutes went (and how the MCP will shrink it):**
   - **Per-step process & handshake overhead (~8–10s × 12 steps):** Each step spawned a fresh `nix develop` shell, new `wss://` TLS + `register` handshake, new `getPointerInputSocket`, and a fresh `scp` TCP/SSH handshake. Keeping **persistent SSAP + pointer WebSockets** and an **SSH ControlMaster / SFTP session** eliminates almost all of this.
   - **Per-step vision/LLM turn latency (~11–13s × 12 turns):** Using a fast decision/vision check for simple state predicates (*"splash gone?"*, *"keyboard open?"*) instead of a full generative turn per frame can reduce a ~7-minute PoC loop to tens of seconds.
