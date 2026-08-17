# Phase 3 — Milestone 3B.1: Android notification bridge

This milestone adds a Home Assistant-local trigger path for legacy Reolink battery cameras whose real-time motion notifications are delivered through the official Android application rather than Message Center.

## Scope

The integration can optionally listen to a Home Assistant Companion **Last Notification** sensor selected in the integration options. It only considers notifications whose Android package is `com.mcu.reolink`, maps the public notification text to the configured camera name, converts Android `post_time` milliseconds to an aware UTC timestamp, creates a deterministic deduplication key, and inserts the normalized event into the existing persistent bounded queue.

The verified Argus 2E notification shape is:

```text
package: com.mcu.reolink
android.title: Camera Alert
android.text: An alarm from atv.
android.bigText: An alarm from atv.
channel_id: push.wav
post_time: <epoch milliseconds>
```

`post_time` is intentionally stored as `notification_post_time`. It is an Android notification publication time and is not represented as the original Reolink alarm timestamp.

## Battery safety

Milestone 3B.1 does **not** wake or connect to the camera. It does not start Baichuan, preview, RTSP, ONVIF, recording search, MP4 download, or Telegram delivery. The queue still has no automatic camera worker.

## Privacy

Only the selected Last Notification entity is observed. Non-Reolink notifications are ignored before their contents are persisted or logged. Diagnostics expose bridge state and the last matched Reolink camera/time, but not arbitrary phone notifications.

## Validation target

After installing the development branch in Home Assistant:

1. Open the Reolink Battery integration options.
2. Select the Android phone's Home Assistant Companion `Last Notification` sensor.
3. Confirm diagnostics show the listener as active.
4. Generate one normal motion notification from the configured Reolink camera.
5. Confirm one event is queued and a duplicate processing attempt is rejected.
6. Confirm no camera-local refresh/session occurred as a side effect.

The next milestone, 3B.2, will consume queued events with a bounded settle delay and a short battery-safe camera session to locate and download the matching SD recording.
