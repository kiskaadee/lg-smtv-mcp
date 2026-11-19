# Standardized E2E Benchmark Baseline (`main` @ `0d67615`)

Standardized pre-#20/#23 baseline executed using [`e2e-benchmark-template.md`](e2e-benchmark-template.md) by subagent `281cc834-58db-4ed5-9d9c-21b1dfd34d67` against `65UR8750PSA` (`WebOS/4.1.0`).

## Preconditions

- **Commit / Branch:** `0d67615` (`main`)
- **Server Config:** `CAPTURE_MAX_WIDTH=unset`, `CAPTURE_JPEG_QUALITY=unset`
- **TV Starting State:** Powered ON at Live TV (`com.webos.app.livetv`), Crunchyroll closed.
- **Timer Boundary:** `T0 = 10:15:27.472`, `T_end = 10:17:55.874` (`-05:00`, excluding initial schema `read_file` setup).

---

## Normalized Scorecard

| Metric | Unstandardized Run (`aac0035`) | Standardized Baseline (`0d67615`) | Notes |
| :--- | :--- | :--- | :--- |
| **Total Timed Span (`T0 -> T_end`)** | `4m 38s` (`278s`, incl. `tv_wake`) | `2m 28s` (`148.40s`) | Zero-waste path via fixed runbook |
| **Total Steps / Captures** | `21` / `15` | `13` / `10` | `3` non-capture + `10` capture calls |
| **Median Capture Size (B)** | `22,824 B` | `19,045 B` | Range: `8,787 B` – `92,524 B` |
| **Disk-Only Offloads (`read_file` turns)** | `11` / `15` | `6` / `10` | All `>= 15,744 B` offloaded; all `<= 9,652 B` stayed inline |
| **Wasted / Retry Steps** | `4` | `0` | — |

---

## Step-by-Step Telemetry Table

| Step # | `T+elapsed` | Tool + Arguments | Tool Duration | Inline / Offload | `size_bytes` | `changed_since_last` | Observed Screen / Result |
|---:|---:|---|---:|:---:|---:|:---:|---|
| 1 | `+6.5s` | `get_state()` | ~3s | N | — | — | TV connected (`com.webos.app.livetv`) |
| 2 | `+9.5s` | `list_apps()` | ~6s | N | — | — | Found `com.crunchyroll.webapp` ("Crunchyroll") |
| 3 | `+18.5s` | `launch(app_id="com.crunchyroll.webapp")` | ~3s | N | — | — | App launched (`returnValue: true`) |
| 4 | `+29.5s` | `screenshot()` | ~4s | Y (inline + disk) | `9,650 B` | `true` | Black screen with orange loading spinner |
| 5 | `+38.5s` | `screenshot()` *(+ `read_file` ~1s)* | ~2s | Y (disk only) | `65,872 B` | `true` | Profile picker (`"¿Quién está viendo esto?"`, `Andres` focused) |
| 6 | `+54.5s` | `keys(keys=["ENTER"], settle_ms=2000, capture=True)` *(+ `read_file` ~1s)* | ~6s | Y (disk only) | `28,663 B` | `true` | Welcome loading screen (`"¡Te damos la bienvenida, Andres!"`) |
| 7 | `+69.5s` | `screenshot()` *(+ `read_file` ~1s)* | ~3s | Y (disk only) | `92,524 B` | `true` | Crunchyroll Home screen (`"Comenzar a Ver"` focused) |
| 8 | `+82.5s` | `keys(keys=["LEFT", "UP", "ENTER"], delay_ms=250, settle_ms=1500, capture=True)` *(+ `read_file` ~1s)* | ~4s | Y (disk only) | `22,346 B` | `true` | Search screen; `"Ataque a los Titanes"` under `"Búsquedas recientes"` |
| 9 | `+97.5s` | `keys(keys=["DOWN", "ENTER"], delay_ms=250, settle_ms=1500, capture=True)` *(+ `read_file` ~1s)* | ~4s | Y (disk only) | `15,744 B` | `true` | `"Ataque a los Titanes"` series page; `"Continuar E1"` focused |
| 10 | `+113.5s` | `keys(keys=["ENTER"], settle_ms=2000, capture=True)` | ~5s | Y (inline + disk) | `9,563 B` | `true` | Black screen with bottom-left orange buffering spinner |
| 11 | `+124.5s` | `screenshot()` | ~7s | Y (inline + disk) | `9,652 B` | `true` | Still buffering (bottom-left orange spinner) |
| 12 | `+130.5s` | `screenshot()` | ~2s | Y (inline + disk) | `8,787 B` | `true` | Solid black DRM frame (playback started) |
| 13 | `+137.5s` | `keys(keys=["DOWN"], settle_ms=800, capture=True)` *(+ `read_file` <1s)* | ~3s | Y (disk only) | `26,617 B` | `true` | Player HUD: `Ataque a los Titanes / E1 - Para ti, en 2000 años -- La caída de Shiganshina, parte 1` (`01:31 / 24:08`) |
