# Reolink Battery RTSP PoC

Experimental parallel integration for researching on-demand Baichuan Live View and, later, an RTSP bridge for battery-powered Reolink cameras.

## Safety / isolation

This PoC uses a separate Home Assistant domain:

`reolink_battery_rtsp_poc`

It is installed **alongside** the normal `reolink_battery` integration. Do not replace or remove the production integration.

The PoC config entry stores only a reference to the existing Reolink Battery config entry. UID, camera-local credentials, and LAN interface remain stored in the production entry. The manual probe also shares the production entry's `local_operation_lock`, preventing a recording download and a Live View probe from using the battery camera at the same time.

## Manual installation

1. From branch `feature/rtsp-poc-parallel-integration`, copy only:

   `custom_components/reolink_battery_rtsp_poc`

   into Home Assistant as:

   `/config/custom_components/reolink_battery_rtsp_poc`

2. Keep the existing directory unchanged:

   `/config/custom_components/reolink_battery`

3. Restart Home Assistant.

4. Go to **Settings → Devices & services → Add integration**.

5. Add **Reolink Battery RTSP PoC**.

6. Select the existing Reolink Battery camera entry when prompted. No credentials need to be entered again.

## First physical test

The PoC exposes one button:

**Probe live stream** / **בדיקת תצוגה חיה**

Pressing it performs a bounded session:

`UID/LAN wake → local login → cmd3 Preview mainStream → 10-second sample → cmd4 stop → logout/close`

It does not create a permanent stream and does not provide an RTSP URL yet.

After the test, download diagnostics for **Reolink Battery RTSP PoC** and inspect the `live_stream_probe` block. The first milestone is reached when the camera accepts Preview and BcMedia is observed, for example:

```text
start_accepted: true
bcmedia_observed: true
video_frames: > 0
h264_frames or h265_frames: > 0
```

## Next milestone

Once real-time BcMedia/H264/H265 is confirmed, the next step is to extract complete video access units and feed them into a local on-demand RTSP/go2rtc bridge.
