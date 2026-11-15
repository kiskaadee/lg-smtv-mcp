# LG webOS Control Frontier (`65UR8750PSA` / `WebOS/4.1.0`)

Empirical notes from probing `192.168.1.11` (`Ethernet`, MAC `7c:64:6c:94:d1:24`) over the local network.

## Transport & Discovery

| Protocol | Endpoint | Purpose | Notes |
| :--- | :--- | :--- | :--- |
| **SSDP** | `239.255.255.250:1900` (UDP) | Discover TV on LAN | `urn:lge-com:service:webos-second-screen:1` returns `Location: http://192.168.1.11:1783/`; `ssdp:all` also reveals DIAL (`:1978`) and Ethernet MAC (`WAKEUP: MAC=...`). |
| **SSAP** | `wss://192.168.1.11:3001` | Primary JSON RPC control bus | Self-signed TLS cert (`CN=LGE TV SSG`); requires `register` handshake and `client-key` saved after on-screen `PROMPT`. |
| **DIAL** | `http://192.168.1.11:36866/apps` | App casting / `<screenId>` lookup | Requires `Origin: https://www.youtube.com` for YouTube (`403` without it). |

---

## Verified SSAP Endpoints

### Working (Tested on `65UR8750PSA`)

- **System info**: `ssap://system/getSystemInfo` → returns `modelName`, `serialNumber`, `receiverType`, `features`.
- **Installed apps**: `ssap://com.webos.applicationManager/listLaunchPoints` → returned 28 apps (`netflix`, `youtube.leanback.v4`, `amazon`, `com.disney.disneyplus-prod`, `com.apple.appletv`, `tv.twitch.tv.starshot.lg`, etc.).
- **Foreground app**: `ssap://com.webos.applicationManager/getForegroundAppInfo` → requires `READ_RUNNING_APPS` permission.
- **Audio status**: `ssap://audio/getStatus` → returns `volume`, `maxVolume`, `mute`, `soundOutput`.
- **Notifications**: `ssap://system.notifications/createToast` with `{"message": "..."}` → displays toast on screen.
- **External inputs**: `ssap://tv/getExternalInputList` → requires `READ_INPUT_DEVICE_LIST` / `CONTROL_INPUT_TV`; returned `HDMI_1`, `HDMI_2`, `HDMI_3`.
- **Launch & close apps**:
  - `ssap://system.launcher/launch` with `{"id": "<app_id>", "params": {...}}`
  - `ssap://system.launcher/close` with `{"id": "<app_id>"}`
- **Deep-linking quirks observed**:
  - **YouTube (`youtube.leanback.v4`)**: Expects `{"contentTarget": "v=<id>&t=<seconds>s"}` (full `https://...` URLs were ignored). If cold-started onto the profile picker, a second launch switches to the video.
  - **Netflix (`netflix`)**: Expects `{"contentTarget": "m=https://www.netflix.com/title/<id>"}` (or `/watch/<episode_id>`). Without `m=`, Netflix hung on loading. Audio/subtitle track selection is not exposed via `params`.
- **Screen capture (via SSAP + Dev Mode SSH)**: `ssap://com.webos.service.capture/executeOneShot` with `{"path": "/tmp/test.jpg", "method": "DISPLAY", "format": "JPG"}` succeeded (`960x540`, `10410`–`12754` bytes):
  - Calling without `"path"` fails with `5 ERROR_NOT_ENOUGH_MEMORY`.
  - `/tmp/test.jpg` is not served over `https://192.168.1.11:3001/tmp/...` (timed out).
  - **Verified workaround**: With **LG Developer Mode** + **Key Server** enabled (SSH on port `9922`, `ssh-rsa` host/pubkey algorithms enabled for the TV host), `scp webos-tv:/tmp/test.jpg .` transfers the `~10 KB` screenshot in `< 1s` (`1.1 MB/s`).
  - Dev Mode shell environment observed: `uid=6739(prisoner) gid=5000(jailer)`, home `/media/developer`, `Linux LGwebOSTV 5.4.268-294.24.papikonda.2 #1 SMP PREEMPT ... aarch64 GNU/Linux` (`1222 MB` RAM).
- **Pointer / D-pad input socket**: `ssap://com.webos.service.networkinput/getPointerInputSocket` returns a second `wss://` `socketPath` accepting plain-text frames (`type:button\nname:HOME|ENTER|BACK|UP|DOWN|LEFT|RIGHT\n\n`). Requires `manifest.permissions` in the `register` payload even when reusing a saved `client-key`.
- **Text input (`ime`)**: `ssap://com.webos.service.ime/insertText` (`{"text": "...", "replace": 0}`) and `ssap://com.webos.service.ime/sendEnterKey` work cleanly once the native webOS on-screen keyboard is open (calling `insertText` before opening the keyboard returns `returnValue: true` without populating the field).
- **Power lifecycle (`turnOff` + Wake-on-LAN)**:
  - `ssap://system/turnOff` cleanly powers the TV into standby (`returnValue: true`).
  - Sending a standard WoL magic packet (`0xff * 6 + mac * 16`) over UDP broadcast (`255.255.255.255:9`) to Ethernet MAC `7c:64:6c:94:d1:24` successfully wakes and powers on the TV from standby.
  - While the TV is in standby/booting, `wss://192.168.1.11:3001` accepts the WebSocket upgrade and immediately closes with `1008 (policy violation) Try Again Later (EWS)`—providing an instant fail-fast / boot-polling signal without waiting for a TCP timeout.

### Blocked on Retail Firmware

- **`ssap://settings/getSystemSettings`**: Returned `401 insufficient permissions` (low-level picture/network/system settings are restricted to signed LG system apps).

### Not Yet Tested on This Device

- **Subscriptions**: `"type": "subscribe"` on `audio/getStatus` and `getForegroundAppInfo` for a persistent client.
