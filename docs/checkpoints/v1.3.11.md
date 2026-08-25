# Reolink Battery Development Checkpoint

**Checkpoint version:** v1.3.11  
**Checkpoint date:** 2026-08-25  
**Repository:** `Dmxsir/ha-reolink-battery`  
**Release target:** `v1.3.11`  
**Primary field-test camera:** Reolink Argus 2E  
**Home Assistant field environment:** HA 2026.8.2 / HA OS 18.2 / Python 3.14.6 / x86_64  
**Camera firmware observed:** `0616_722_52_478`  
**Camera hardware observed:** `BIPC_36S7616MC94`

---

## 1. Purpose

This is the authoritative handoff for continuing the Reolink Battery project in a new ChatGPT/Codex session. Read it completely before changing recording transport, Live View arbitration, retry timing, or queue policy.

The basic Argus 2E protocol is already physically proven. Current work is reliability hardening around battery-camera wake state, media-transfer stalls, and interaction between on-demand Live View and automatic recording.

Every future version bump must update this root `CHECKPOINT.md` and create an identical archive at `docs/checkpoints/v<version>.md`. `tests/test_release_checkpoint.py` and the GitHub release workflow enforce this.

---

## 2. Product goal and battery-safety model

Automatic workflow:

```text
Reolink motion
  -> official Reolink Android notification
  -> Home Assistant Companion Last Notification sensor
  -> Reolink Battery notification bridge
  -> persistent event queue
  -> camera remains closed during settle period
  -> local UID wake/discovery
  -> legacy local authentication
  -> FileInfo recording lookup
  -> cmd13 prepare/download handle
  -> cmd8 full MP4 transfer
  -> exact authoritative-size verification
  -> fsync + atomic .part -> .mp4 rename
  -> reolink_battery_recording_ready
  -> optional Telegram automation
```

Battery-safety invariants:

1. No continuous polling of the sleeping camera for motion.
2. Android push is the automatic motion trigger.
3. Initial automatic recording settle remains 60 seconds unless field evidence justifies a separate experiment.
4. No simultaneous local recording and Live View Baichuan sessions.
5. Recording gets priority once a fresh event reaches its processing point.
6. Automatic retries are bounded.
7. Stale backlog does not repeatedly wake the camera.
8. Partial files never become final recordings.
9. Exact expected size is required before `recording_ready` fires.

---

## 3. Authentication and tested device conclusions

Test device:

```text
Model: Argus 2E
Firmware: 0616_722_52_478
Hardware: BIPC_36S7616MC94
Auth path: legacy_local_credential
```

Established conclusions:

- Reolink cloud login works.
- The camera is bound to the cloud account.
- Cloud credentials alone are not sufficient for the local recording path.
- A local camera administrator password is required.
- The tested unit does not expose a usable protocol-3/sigV3 path.
- LAN/P2P UID discovery works.
- Legacy local login works on the real camera.
- Battery-camera wake/readiness races can still cause transient auth/FileInfo failures; do not automatically interpret every `AUTH_ERROR` as a wrong password.

---

## 4. Official SDK oracle and physically proven recording protocol

The official Windows SDK was used as an oracle on the same Argus 2E.

Observed official behavior:

```text
LAN connection -> OPEN_SUCCESS
recording query -> response code 0
cmd16 before download
cmd13 -> accepted
cmd8 -> accepted
full recording bytes received
```

One SDK clip completed at exactly:

```text
10,521,742 / 10,521,742 bytes
```

The portable implementation later reproduced full recording download behavior.

Validated transport characteristics retained in stable code:

- FileInfo recording lookup and candidate selection.
- cmd13 prepare request.
- cmd13 returned download handle.
- cmd8 using proven full-high `FileInfo` layout.
- forced `mainStream` on the tested camera.
- same authenticated local session handoff.
- fresh heartbeat transaction IDs after login.
- P2P heartbeat approximately every 1 second.
- periodic UDP ACK approximately every 10 ms.
- inclusive-highest ACK behavior derived from physical/official-client work.
- authoritative expected recording size.
- `.part` temporary file.
- valid MP4 `ftyp` check.
- exact-size final validation.
- fsync before finalization.
- atomic rename only after success.
- failed/incomplete `.part` cleanup.

Do **not** rewrite cmd13/cmd8 framing, heartbeat, ACK bitmap semantics, payload offsets, or exact-size verification just because a worker/session-management test fails. Those components have already produced successful full downloads and successful Telegram end-to-end delivery.

Retained diagnostic transport baselines:

```text
v0.1.2-beta.40
  fresh post-auth heartbeat TIDs
  ~1s heartbeat
  ~10ms periodic-only inclusive-highest ACK
  reliable cmd13/cmd8
  full-high/mainStream

v0.1.2-beta.45
  authoritative cmd13 size used to size verified collector
  >16 MiB verified recordings supported
  128 MiB hard safety cap retained
```

Stable code intentionally still inherits from beta-named modules. Do not remove them as part of an unrelated reliability fix.

---

## 5. Notification bridge and queue policy

The Android notification bridge is considered operational. Field diagnostics have repeatedly shown:

```text
configured: true
listener_active: true
last_event_matched: true
last_camera_mapped: true
last_event_queued: true
duplicate_rejected: false
```

Automatic worker constants/policy:

```text
RECORDING_SETTLE_SECONDS = 60.0
MAX_ATTEMPTS_PER_TRIGGER = 3
RETRY_DELAYS_SECONDS = (30.0, 60.0)
AUTOMATIC_EVENT_MAX_AGE_SECONDS = 600
selection_policy = newest_activated_fresh_first
deferred_rearm_policy = explicit_only
startup_recovery_policy = newest_fresh_non_deferred_only
retry_preemption_policy = newer_notification_before_retry
```

Important behavior:

- newest explicitly activated fresh Android event wins;
- stale backlog is retained but does not wake the camera;
- exhausted events are persistently deferred;
- deferred events are not silently rearmed;
- startup recovery selects at most the newest fresh non-deferred event;
- a newer notification may preempt an older event during retry wait;
- an active camera/download attempt is not cancelled merely because newer motion arrives.

Recording dedupe policy:

```text
persistent_candidate_fingerprint_before_cmd13
```

Multiple notifications mapping to the same physical SD clip should not download/send the same MP4 repeatedly.

---

## 6. Real failure leading to v1.3.8

A real motion event progressed through notification, queue, UID, auth, FileInfo, cmd13 and cmd8. MP4 transfer started but the camera explicitly closed the session early.

Observed telemetry:

```text
xml_reported_size:          9,555,011
file_bytes_written:         3,342,336
mp4_ftyp_valid:             true
remote_disconnect_observed: true
termination_reason:         connection_closed
elapsed_seconds:            ~46.93
cmd13_udp_ack_received:     true
cmd13_udp_retransmit_count: 0
cmd8_udp_ack_received:      true
cmd8_udp_retransmit_count:  0
```

Integrity behavior was correct:

```text
part_created: true
final_size_match: false
atomic_rename_completed: false
file_saved: false
part_removed_on_failure: true
```

v1.3.8 added a narrow classifier:

```text
STREAM_REMOTE_DISCONNECT_INCOMPLETE
```

Eligibility requires:

```text
remote_disconnect_observed == true
termination_reason == connection_closed
file_bytes_written > 0
expected_size > file_bytes_written
```

Once this signature occurs, remaining bounded retries use 3 and 6 seconds instead of 30/60, attempting to reuse the Argus awake window.

---

## 7. Real failure leading to v1.3.9

A later physical run produced another partial-transfer signature without explicit remote disconnect:

```text
event_time:                2026-08-25T14:18:28.832000+00:00
attempt_time:              2026-08-25T14:19:28.832908+00:00
uid_resolve_succeeded:     true
uid_resolve_elapsed_ms:    839.882
termination_reason:        idle_timeout
stream_elapsed_seconds:    51.328
file_bytes_written:        1,261,568
expected_size:             9,314,178
remote_disconnect:         false
```

v1.3.9 added:

```text
STREAM_IDLE_TIMEOUT_INCOMPLETE
```

Eligibility requires:

```text
file_bytes_written > 0
expected_size > file_bytes_written
termination_reason == idle_timeout
```

The same 3/6-second bounded fast retry is used. Zero-byte timeouts and unrelated failures retain the base 30/60 policy.

During that field session, a newer notification also correctly preempted the older retry wait:

```text
retry_preemptions: 1
last_preempted_event_time:  2026-08-25T14:18:28.832000+00:00
last_preempting_event_time: 2026-08-25T14:20:28.885000+00:00
```

That behavior is intentional.

---

## 8. Real failure leading to v1.3.10 — Live View lock starvation

After v1.3.9, a new field capture exposed a completely different issue.

Latest motion notification:

```text
2026-08-25T15:53:54.887000+00:00
18:53:54 local Asia/Jerusalem
```

Worker attempt began exactly after the 60-second settle:

```text
2026-08-25T15:54:54.887984+00:00
18:54:54 local
```

Diagnostics were exported around 19:12 local. The worker had nominally been running for more than 17 minutes, but transport had never started:

```text
recording_worker.running: true
attempts: 1
retries: 0
last_failure_stage: null
uid_resolve.send_rounds: 0
uid_resolve.datagrams_sent: 0
uid_resolve.elapsed_ms: null
cmd13_frames: 0
cmd8_frames: 0
file_write_attempted: false
```

At exactly the same time Live View showed:

```text
active: true
video_consumers: 1
sessions_started: 1
sessions_completed: 0
```

Code inspection confirmed both use the same `runtime.local_operation_lock`:

```text
Live View holds lock for entire async_stream_media session.
Recording worker takes the same lock before async_prepare_download_for_event.
```

Therefore recording was starved before UID discovery. This was not an auth, FileInfo, cmd13/cmd8 or UDP failure.

v1.3.10 introduced recording priority:

1. recording requests `live_hub.async_pause_for_recording()`;
2. active Live View producer receives its normal stop event;
3. reconnecting Live View producers are gated while priority depth is non-zero;
4. recording then obtains the existing shared lock normally;
5. after the attempt the worker calls `async_resume_after_recording()`;
6. one local Baichuan session at a time remains invariant.

v1.3.10 diagnostics added:

```text
recording_priority_active
recording_priority_depth
recording_preemptions
```

---

## 9. Field report leading to v1.3.11 — Live View stopped working

After v1.3.10 was released, the field report was:

```text
"גם הלייב פייד הפסיק לעבוד"
Live feed also stopped working.
```

Review of the v1.3.10 Live View lifecycle exposed a concrete regression risk.

`async_pause_for_recording()` correctly stopped only the active camera-side producer, but the producer's common cleanup path `_finish_producer()` still did this unconditionally:

```text
for every video/audio queue:
    queue.put_nowait(None)
```

The HTTP media view interprets `None` as EOF, breaks its response loop, unsubscribes the queue and closes the HTTP response. Therefore a recording preemption did not merely yield the camera lease; it also deliberately terminated the H264/AAC HTTP sources feeding go2rtc.

That made Live View recovery dependent on an EOF/reconnect cycle in go2rtc. Even if go2rtc normally retries sources, this coupling is unnecessary and can leave the Home Assistant Live View dead after recording arbitration.

This is a lifecycle regression in v1.3.10, not evidence that the Baichuan live media protocol itself stopped working.

---

## 10. v1.3.11 design — preserve consumers, replace only producer

v1.3.11 keeps recording priority but separates two concepts:

```text
camera producer lifecycle
!=
HTTP/go2rtc consumer lifecycle
```

During recording preemption:

```text
Live View HTTP/go2rtc consumers remain subscribed/open
        ↓
active camera-side producer receives stop_event
        ↓
producer releases local_operation_lock
        ↓
recording performs UID/auth/FileInfo/cmd13/cmd8
        ↓
recording priority ends
        ↓
fresh camera producer starts
        ↓
existing preserved consumers receive media again
```

Implementation rule in `ReolinkBatteryLiveHub._finish_producer()`:

```text
preserve_consumers = recording_priority_depth > 0
```

If recording priority is active:

- do **not** enqueue `None`/EOF to video or audio queues;
- retain queues registered by existing go2rtc HTTP requests;
- count preserved finish/consumer telemetry;
- clear only producer task and producer stop event.

If recording priority is not active:

- retain the previous normal behavior;
- publish `None` to consumers so a genuine Live View end/error still closes the HTTP stream cleanly.

`async_resume_after_recording()` starts a new producer whenever preserved/queued consumers exist and recording priority depth returns to zero.

This preserves the v1.3.10 starvation fix without requiring concurrent camera sessions.

---

## 11. New v1.3.11 Live View diagnostics

In addition to the v1.3.10 fields:

```text
recording_priority_active
recording_priority_depth
recording_preemptions
```

v1.3.11 adds:

```text
recording_preserved_finishes
recording_preserved_consumers
```

Interpretation:

- `recording_preemptions >= 1`: an active Live View producer was asked to yield for recording.
- `recording_preserved_finishes >= 1`: producer cleanup occurred while recording priority was active and consumer queues were intentionally preserved.
- `recording_preserved_consumers`: cumulative number of H264/AAC consumer queues preserved across those producer finishes.
- `recording_priority_active` should return to `false` after each recording attempt.
- `recording_priority_depth` should normally return to `0` after an attempt.

These fields are secret-safe and contain no credentials, addresses or filenames.

---

## 12. What v1.3.11 deliberately does NOT change

Do not attribute unrelated recording behavior to this patch. v1.3.11 does not intentionally change:

- Android notification matching;
- persistent queue/backlog policy;
- 600-second automatic freshness window;
- 60-second initial settle;
- three-attempt bound;
- normal 30/60 retry timing;
- v1.3.8/v1.3.9 partial-stream 3/6 fast recovery;
- newer-motion retry preemption;
- FileInfo selection;
- cmd13 framing/body;
- cmd8 full-high/mainStream framing/body;
- returned handle use;
- authenticated recording session handoff;
- P2P heartbeat cadence;
- fresh heartbeat TIDs;
- periodic UDP ACK cadence;
- inclusive-highest ACK behavior;
- verified collector sizing;
- exact-size MP4 acceptance;
- atomic file finalization;
- partial-file deletion;
- recording fingerprint deduplication;
- single local camera session invariant.

The patch is strictly about keeping Live View HTTP/go2rtc consumers alive while the camera producer temporarily yields for recording.

---

## 13. Important source files

Integration/runtime wiring:

```text
custom_components/reolink_battery/__init__.py
custom_components/reolink_battery/diagnostics.py
custom_components/reolink_battery/go2rtc_bridge.py
```

Live View:

```text
custom_components/reolink_battery/camera.py
custom_components/reolink_battery/live_http.py
custom_components/reolink_battery/live_stream.py
```

Automatic recording worker:

```text
custom_components/reolink_battery/recording_worker.py
custom_components/reolink_battery/recording_worker_v138.py
```

Recording/download chain:

```text
custom_components/reolink_battery/recording_probe.py
custom_components/reolink_battery/recording_download_probe.py
custom_components/reolink_battery/recording_download_probe_beta20.py
custom_components/reolink_battery/recording_download_probe_beta21.py
custom_components/reolink_battery/recording_download_beta22.py
```

Tests relevant to current work:

```text
tests/test_recording_live_priority.py
tests/test_recording_stream_recovery_v138.py
tests/test_recording_backlog_policy.py
tests/test_notification_bridge.py
tests/test_integration_load.py
tests/test_release_checkpoint.py
```

---

## 14. Important interpretation rules for diagnostics

### A. `recording_worker.running: true` is not proof of network activity

A worker can be inside an attempt while still blocked before UID discovery. Always correlate with `uid_resolve.*`, stream probe and Live View priority state.

### B. Current stream probe may be zeroed during a newer attempt

The worker resets stream telemetry at attempt start. Use `recording_worker.prior_attempt` for immediately previous transfer evidence.

### C. Do not treat historical UDP unresolved count as final active loss

Prefer current-at-disconnect fields such as:

```text
udp_current_missing_packet_count_at_disconnect
udp_expected_next_seq_at_disconnect
udp_highest_network_seq_seen
udp_buffered_out_of_order_at_disconnect
udp_max_gap_recovery_ms
```

### D. Partial MP4 remains invalid even if `ftyp` is valid

A valid MP4 prefix is not sufficient. Authoritative expected size must match exactly before atomic rename and ready event.

### E. Newer-motion retry preemption is intentional

If `retry_preemptions > 0` and timestamps show a newer fresh event, an old event may legitimately not receive its planned retry.

### F. Live View interruption during recording may still be visible

v1.3.11 preserves HTTP/go2rtc consumers, but the actual camera producer must still stop while recording owns the local camera lease. The image may pause/freeze temporarily. The required improvement is that the feed resumes rather than being permanently ended by an intentional EOF.

---

## 15. Exact next physical test after v1.3.11

1. Install v1.3.11 through HACS.
2. Restart Home Assistant.
3. Confirm diagnostics show integration version `1.3.11`.
4. First test Live View by itself and confirm it opens normally.
5. Keep Live View open.
6. Trigger one real motion notification in front of `atv`.
7. Avoid additional motion if possible so retry timing remains interpretable.
8. Keep the dashboard/Live View open through the 60-second recording settle point.
9. Allow the recording attempt to complete or clearly fail.
10. Confirm the Live View resumes after the recording arbitration.
11. Export integration diagnostics.

Expected arbitration fields if Live View was active when recording began:

```text
recording_preemptions >= 1
recording_preserved_finishes >= 1
recording_preserved_consumers >= 1
```

After the attempt:

```text
recording_priority_active: false
recording_priority_depth: 0
```

The recording worker should not remain stuck for minutes with:

```text
running: true
attempts > 0
uid_resolve.send_rounds: 0
uid_resolve.elapsed_ms: null
```

UID discovery should begin after the Live View producer yields.

Successful recording target:

```text
file_saved: true
final_size_match: true
atomic_rename_completed: true
recording_worker.last_file_saved: true
recording_worker.last_ready_event_fired: true
```

If the transfer starts but fails with explicit remote close, expect possible:

```text
STREAM_REMOTE_DISCONNECT_INCOMPLETE
```

and a ~3-second fast retry unless newer motion preempts it.

If the transfer starts but stalls after real bytes, expect possible:

```text
STREAM_IDLE_TIMEOUT_INCOMPLETE
```

and the same fast recovery policy.

---

## 16. Fields to inspect in the next diagnostics

Version:

```text
custom_components.reolink_battery.version
integration_manifest.version
```

Live View:

```text
live_view.session.active
live_view.session.video_consumers
live_view.session.audio_consumers
live_view.session.sessions_started
live_view.session.sessions_completed
live_view.session.recording_priority_active
live_view.session.recording_priority_depth
live_view.session.recording_preemptions
live_view.session.recording_preserved_finishes
live_view.session.recording_preserved_consumers
live_view.session.last_failure_stage
live_view.session.last_failure_type
live_view.session.last_session
```

Recording worker:

```text
recording_worker.running
recording_worker.pending_trigger
recording_worker.waiting_camera_closed
recording_worker.attempts
recording_worker.retries
recording_worker.completed
recording_worker.retry_preemptions
recording_worker.last_event_time
recording_worker.last_attempt_time
recording_worker.last_failure_stage
recording_worker.last_failure_type
recording_worker.last_file_saved
recording_worker.last_file_size
recording_worker.last_ready_event_fired
recording_worker.prior_attempt.*
```

UID wake:

```text
recording_worker.uid_resolve.timeout_seconds
recording_worker.uid_resolve.resend_interval_seconds
recording_worker.uid_resolve.send_rounds
recording_worker.uid_resolve.datagrams_sent
recording_worker.uid_resolve.elapsed_ms
recording_worker.uid_resolve.succeeded
```

Recording stream:

```text
download_prepare.stream_probe.cmd13_frames
download_prepare.stream_probe.cmd8_frames
download_prepare.stream_probe.cmd8_attempted
download_prepare.stream_probe.file_write_attempted
download_prepare.stream_probe.file_bytes_written
download_prepare.stream_probe.xml_reported_size
download_prepare.stream_probe.final_size_match
download_prepare.stream_probe.file_saved
download_prepare.stream_probe.termination_reason
download_prepare.stream_probe.elapsed_seconds
download_prepare.stream_probe.remote_disconnect_observed
download_prepare.stream_probe.cmd13_udp_ack_received
download_prepare.stream_probe.cmd8_udp_ack_received
download_prepare.stream_probe.udp_current_missing_packet_count_at_disconnect
download_prepare.stream_probe.udp_max_gap_recovery_ms
```

---

## 17. Current release/checkpoint policy

For every future release:

1. bump `custom_components/reolink_battery/manifest.json`;
2. update `CHANGELOG.md`;
3. update root `CHECKPOINT.md` fully;
4. create `docs/checkpoints/v<version>.md` with identical bytes;
5. include new physical evidence, decisions and next validation target;
6. run compile and all regression tests;
7. pass HACS validation;
8. do not publish release if checkpoint consistency fails.

The checkpoint is a release requirement, not optional documentation.

---

## 18. New-chat kickoff prompt

Copy this into a new chat:

```text
Continue development of Dmxsir/ha-reolink-battery.

First read CHECKPOINT.md completely and treat it as the authoritative handoff.
Then inspect current main and the latest diagnostics I provide before making a
new protocol change.

Current target/release state is v1.3.11 on Reolink Argus 2E.
The basic recording protocol is physically proven: UID/legacy auth, FileInfo,
cmd13 handle, full-high/mainStream cmd8, 1-second heartbeat, periodic inclusive-
highest UDP ACK, exact-size MP4 verification and end-to-end Telegram have all
worked on real hardware.

v1.3.8 added fast recovery for partial transfer + connection_closed.
v1.3.9 added fast recovery for partial transfer + idle_timeout.
v1.3.10 fixed recording starvation when an indefinitely active Live View held
local_operation_lock before UID discovery by giving recording priority.
v1.3.11 fixes the Live View regression in that arbitration: camera producer
still yields, but H264/AAC HTTP/go2rtc consumer queues are preserved and a fresh
producer resumes against them after recording instead of sending intentional EOF.

Do not rewrite cmd13/cmd8/heartbeat/UDP ACK or change several timing variables at
once without new field evidence.

For every future version update, update CHECKPOINT.md and create the matching
byte-identical docs/checkpoints/v<version>.md before release.
```

---

## 19. Handoff summary

```text
Android notification ingestion                  working
persistent queue / stale backlog policy         working
recording fingerprint dedupe                     working
UID discovery / legacy auth                      physically proven
FileInfo lookup                                  physically proven
cmd13/cmd8 request shape                         physically proven
full exact MP4 download                          physically proven
recording-ready / Telegram end-to-end            physically proven
partial remote-close fast recovery               implemented v1.3.8
partial idle-timeout fast recovery               implemented v1.3.9
Live View starving recording lock                addressed v1.3.10
recording priority / single local session        retained
v1.3.10 intentional EOF to go2rtc consumers      identified regression risk
preserve consumers across recording preemption   implemented v1.3.11
v1.3.11 physical Live View resume validation     NEXT REQUIRED TEST
```

**Next action:** install v1.3.11, verify Live View alone, then keep Live View open through one real motion event and confirm recording gets priority while the same Live View consumer path resumes afterward. Send diagnostics after that field test before changing recording transport again.
