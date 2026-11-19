# Post-#20 Standardized E2E Benchmark (`main` @ `09618be`)

Post-**#20** benchmark executed using [`e2e-benchmark-template.md`](e2e-benchmark-template.md) by subagent `9d82e846-0c13-4c82-8f8f-2780d34ad173` against `65UR8750PSA` (`WebOS/4.1.0`), compared against [`crunchyroll-e2e-baseline-0d67615.md`](crunchyroll-e2e-baseline-0d67615.md).

## Preconditions

- **Commit / Branch:** `09618be` (`main`, PR `#24`)
- **Server Config:** `CAPTURE_MAX_WIDTH=480`, `CAPTURE_JPEG_QUALITY=55`
- **TV Starting State:** Powered ON at Live TV (`com.webos.app.livetv`), Crunchyroll closed.
- **Timer Boundary:** `T0 = 11:16:18.965`, `T_end = 11:19:06.423` (`-05:00`, excluding initial schema `read_file` setup).

---

## Normalized Scorecard

| Metric | Baseline (`0d67615`, raw `960x540`) | Post-**#20** (`09618be`, `480px`, `q=55`) | Delta |
| :--- | :--- | :--- | :--- |
| **Total Timed Span (`T0 -> T_end`)** | `148.40s` (`13` steps) | `167.46s` (`17` steps) | `+19.06s` *(4 extra steps: Crunchyroll auto-resumed previous show + 1 `ENTER` retry)* |
| **Average Time per Step** | `11.42s / step` | `9.85s / step` | `-1.57s / step` |
| **Total Steps / Captures** | `13` / `10` | `17` / `14` | `+4` / `+4` |
| **Median Capture Size (B)** | `19,045 B` | `5,893 B` | `-69.1%` |
| **Disk-Only Offloads (`read_file` turns)** | `6 / 10` (`60%`) | `1 / 14` (`7%`) | `-53 pp` *(only `10,951 B` splash offloaded; all `<= 8,421 B` stayed inline)* |
| **Wasted / Retry Steps** | `0` | `1` | `+1` *(Step 6 `ENTER` on profile screen)* |

---

## Step-by-Step Telemetry Table

| Step # | `T+elapsed` | Tool + Arguments | Tool Duration | Inline / Offload | `size_bytes` | `changed_since_last` | Observed Screen / Result |
|---:|---:|---|---:|:---:|---:|:---:|---|
| 1 | `~8s` | `get_state()` | ~2s | N | — | — | `connected: true`, `foreground_app: com.webos.app.livetv` |
| 2 | `~11s` | `list_apps()` | ~5s | N | — | — | Found `com.crunchyroll.webapp` ("Crunchyroll") |
| 3 | `~19s` | `launch(app_id="com.crunchyroll.webapp")` | ~4s | N | — | — | `returnValue: true` |
| 4 | `~29s` | `screenshot()` *(+ `read_file`)* | ~4s | Y (disk only) | `10,951 B` | `true` | Crunchyroll splash screen |
| 5 | `~44s` | `screenshot()` | ~3s | Y (inline + disk) | `8,421 B` | `true` | Profile picker (`Andres` focused) |
| 6 | `~53s` | `keys(["ENTER"], settle_ms=1800, capture=True)` | ~4s | Y (inline + disk) | `8,421 B` | `false` | Still on profile picker (`Andres` focused) |
| 7 | `~64s` | `keys(["ENTER"], settle_ms=2000, capture=True)` | ~5s | Y (inline + disk) | `2,782 B` | `true` | Black loading spinner |
| 8 | `~73s` | `screenshot()` | ~4s | Y (inline + disk) | `2,812 B` | `true` | Black loading spinner |
| 9 | `~80s` | `screenshot()` | ~2s | Y (inline + disk) | `3,548 B` | `true` | Black DRM frame (auto-resumed previous show) |
| 10 | `~89s` | `keys(["DOWN"], settle_ms=1000, capture=True)` | ~4s | Y (inline + disk) | `6,454 B` | `true` | Player HUD: `Hello, I am a Witch... E1` |
| 11 | `~98s` | `keys(["BACK"], settle_ms=1500, capture=True)` | ~5s | Y (inline + disk) | `3,118 B` | `true` | Home screen loading with left sidebar |
| 12 | `~105s` | `keys(["LEFT"], settle_ms=800, capture=True)` | ~3s | Y (inline + disk) | `7,469 B` | `true` | Left sidebar open, `Inicio` focused |
| 13 | `~114s` | `keys(["UP", "ENTER"], settle_ms=1000, capture=True)` | ~4s | Y (inline + disk) | `5,499 B` | `true` | Search screen, `Ataque a los Titanes` under `Búsquedas recientes` |
| 14 | `~123s` | `keys(["DOWN", "ENTER"], settle_ms=1500, capture=True)` | ~4s | Y (inline + disk) | `7,545 B` | `true` | Series page `Ataque a los Titanes`, `Continuar E1` focused |
| 15 | `~139s` | `keys(["ENTER"], settle_ms=2000, capture=True)` | ~4s | Y (inline + disk) | `2,809 B` | `true` | Black loading spinner |
| 16 | `~146s` | `screenshot()` | ~2s | Y (inline + disk) | `3,540 B` | `true` | Black DRM frame (`16+` rating overlay) |
| 17 | `~155s` | `keys(["DOWN"], settle_ms=800, capture=True)` | ~4s | Y (inline + disk) | `6,287 B` | `true` | Player HUD: `Ataque a los Titanes` / `E1 - Para ti, en 2000 años -- La caída de Shiganshina, parte 1` (`08:02 / 17:37`) |
