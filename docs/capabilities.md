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
- **Screen capture (local only)**: `ssap://com.webos.service.capture/executeOneShot` with `{"path": "/tmp/test.jpg", "method": "DISPLAY", "format": "JPG"}` succeeded (`960x540`, `12754` bytes), **but**:
  - Calling without `"path"` fails with `5 ERROR_NOT_ENOUGH_MEMORY`.
  - `/tmp/test.jpg` is not served over `https://192.168.1.11:3001/tmp/...` (timed out); reading it off the TV requires **LG Developer Mode** SSH (port `9922`) or external HDMI capture.

### Blocked on Retail Firmware

- **`ssap://settings/getSystemSettings`**: Returned `401 insufficient permissions` (low-level picture/network/system settings are restricted to signed LG system apps).

### Not Yet Tested on This Device

- **Pointer / D-pad input socket**: `ssap://com.webos.service.networkinput/getPointerInputSocket` (second WebSocket using plain-text `type:button` / `type:move` / `type:click` frames).
- **Text input**: `ssap://com.webos.service.ime/insertText` & `sendEnterKey` (works in native webOS inputs; some third-party apps use custom keyboards).
- **Subscriptions**: `"type": "subscribe"` on `audio/getStatus` and `getForegroundAppInfo` for a persistent client.
- **Wake-on-LAN**: Magic UDP packet to MAC `7c:64:6c:94:d1:24` to power on from standby.
