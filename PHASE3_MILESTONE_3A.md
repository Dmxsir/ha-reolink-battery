# Phase 3 Milestone 3A

Milestone 3A is limited to cloud event ingestion. The camera recording worker,
MP4 download, and Telegram delivery remain disabled.

## Implemented cloud path

The integration follows the Android application's device-alarm Message Center
path:

```text
POST /v2/message-center/devices/msgs/query
-> STM v1 decode when present
-> event normalization
-> event-id deduplication
-> bounded private Home Assistant storage
```

The bounded query uses a recent time window and the selected standalone IPC's
channel-scoped UID filter. `nextToken` is observed only as response pagination
metadata; it is not persisted as a polling cursor.

STM v1 derives an AES-128 key and IV from HMAC-SHA256 using the response time
and account user ID, then decrypts AES-CFB with no padding. No key, IV, token,
credential, encrypted payload, personal message content, or full UID is logged
or exposed in diagnostics.

## Normalization and persistence

- `event.id` is the deduplication key.
- A standalone camera's channel-scoped `<uid>_00` is normalized back to its
  configured main UID.
- Numeric epoch seconds/milliseconds and ISO timestamps become aware UTC.
- `aiType` accepts a scalar, list, null, or an absent value.
- `details.clip` is deliberately ignored as recording identity.
- The persistent queue is bounded and private.

## Safety boundary

The runtime cloud poller imports no camera transport and cannot wake or connect
to the camera. It performs no HTTP request to the camera, Baichuan operation,
RTSP/ONVIF access, recording search, MP4 download, snapshot, preview, or
Telegram delivery. No entity platform is created.

## Validation state

Deterministic offline coverage includes the Android-equivalent STM v1 fixture,
plaintext and empty responses, malformed STM, scalar/list `aiType`, missing
optional fields, UTC normalization, duplicate suppression, queue insertion,
and the exact bounded request shape. Real Home Assistant validation is recorded
here after one natural cloud event enters the queue.
