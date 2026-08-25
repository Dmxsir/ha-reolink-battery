# Reolink Battery Development Checkpoint

**Checkpoint version:** v1.3.12  
**Checkpoint date:** 2026-08-25  
**Repository:** `Dmxsir/ha-reolink-battery`  
**Release target:** `v1.3.12`  
**Primary field-test camera:** Reolink Argus 2E  
**Home Assistant field environment:** HA 2026.8.2 / HA OS 18.2 / Python 3.14.6 / x86_64  
**Camera firmware observed:** `0616_722_52_478`  
**Camera hardware observed:** `BIPC_36S7616MC94`

---

## 1. Purpose of this checkpoint

This is the authoritative handoff for continuing the Reolink Battery project in a new ChatGPT/Codex session. Read it completely before changing recording transport, retry timing, queue semantics, Live View arbitration, or the new manual recovery controls.

The underlying Argus 2E recording protocol is already physically proven. Current development is reliability hardening and Home Assistant workflow usability, not rediscovery of cmd13/cmd8.

Every future version bump must:

1. update root `CHECKPOINT.md`;
2. create a byte-identical archive at `docs/checkpoints/v<version>.md`;
3. update `CHANGELOG.md` and `manifest.json`;
4. pass `tests/test_release_checkpoint.py`;
5. pass compile, regression, integration-load and HACS validation;
6. only then publish the GitHub release.

The release workflow itself verifies checkpoint consistency.

---

## 2. Product goal

Build a battery-safe Home Assistant integration for Reolink battery cameras, validated primarily against the physical **Argus 2E**.

Automatic recording workflow:

```text
Reolink motion
  -> official Reolink Android notification
  -> Home Assistant Companion Last Notification sensor
  -> Reolink Battery notification bridge
  -> persistent event queue
  -> camera remains closed during recording settle
  -> local UID wake/discovery
  -> legacy local authentication
  -> FileInfo recording lookup
  -> cmd13 prepare/download handle
  -> cmd8 full MP4 transfer
  -> exact authoritative-size verification
  -> fsync + atomic .part -> .mp4 finalization
  -> reolink_battery_recording_ready HA event
  -> optional Telegram automation
```

On-demand Live View is a separate path through the integration/go2rtc. Automatic motion detection does **not** require continuous RTSP/ONVIF/polling.

---

## 3. Battery-safety invariants

Preserve these unless later real-hardware evidence deliberately changes the design:

1. No continuous camera polling for automatic motion detection.
2. Android push notification is the automatic trigger.
3. Initial automatic recording settle remains 60 seconds.
4. Only one local Baichuan camera session at a time.
5. Recording gets priority over an indefinitely open Live View once a recording attempt is ready.
6. Automatic retries remain bounded to three attempts per event.
7. Stale backlog does not automatically wake the camera merely because Home Assistant restarted.
8. Deferred backlog is not silently rearmed by unrelated new motion.
9. A manual recovery button may explicitly bypass automatic freshness rules because the user deliberately requested the wake/recovery work.
10. Partial MP4 files never become final recordings.
11. Exact authoritative expected size is required before final save/ready event.
12. Completed-recording fingerprints prevent duplicate downloads/sends of the same physical SD clip.

---

## 4. Authentication and tested hardware conclusions

Test device:

```text
Model: Argus 2E
Firmware: 0616_722_52_478
Hardware: BIPC_36S7616MC94
Auth path: legacy_local_credential
```

Established conclusions:

- Reolink cloud login works.
- Camera is bound to the cloud account.
- Cloud account credentials alone are insufficient for local recording download.
- A local camera administrator password is required.
- The tested unit does not expose a usable protocol-3/sigV3 path.
- UID discovery/wake over the local/P2P path works.
- Legacy local authentication works on the physical camera.
- Battery-camera wake/readiness races can still produce transient auth/FileInfo failures; do not automatically interpret every `AUTH_ERROR` as a wrong password.

Current Python requirements:

```text
pycryptodomex==3.23.0
reolink-aio==0.21.8
```

---

## 5. Official SDK oracle and physically proven recording protocol

The official Windows SDK was previously used as an oracle against the same Argus 2E.

Observed official behavior:

```text
LAN connection -> OPEN_SUCCESS
recording query -> response code 0
cmd16 observed before download
cmd13 -> accepted
cmd8 -> accepted
full recording bytes received
```

One SDK recording completed exactly at:

```text
10,521,742 / 10,521,742 bytes
```

The portable integration later reproduced complete end-to-end recording download on the real camera.

Validated stable transport behavior includes:

- FileInfo recording lookup and candidate selection;
- cmd13 prepare request;
- cmd13 returned download handle;
- cmd8 proven full-high `FileInfo` layout;
- forced `mainStream` on the tested unit;
- same authenticated recording-session handoff;
- fresh heartbeat transaction IDs after login;
- P2P heartbeat approximately every 1 second;
- periodic UDP ACK approximately every 10 ms;
- inclusive-highest ACK semantics established from physical/official-client work;
- authoritative recording size;
- `.part` temporary file;
- MP4 `ftyp` validation;
- fsync;
- exact-size verification;
- atomic rename only after success;
- partial-file deletion on failure;
- `reolink_battery_recording_ready` only after a verified final MP4 exists.

### Critical transport rule

Do **not** rewrite cmd13/cmd8 framing, heartbeat cadence, UDP ACK bitmap semantics, payload offsets, handle use or exact-size acceptance merely because a later worker/UI/session-management test fails. These components have already produced successful full downloads and successful Telegram delivery.

Retained diagnostic transport references:

```text
v0.1.2-beta.40
  fresh post-auth heartbeat TIDs
  ~1-second heartbeat
  ~10 ms periodic-only inclusive-highest ACK
  reliable cmd13/cmd8
  full-high/mainStream

v0.1.2-beta.45
  authoritative cmd13 size used for verified collector sizing
  recordings larger than former 16 MiB diagnostic ceiling
  128 MiB verified hard cap retained
```

Stable code intentionally still inherits behavior from beta-named modules. Do not remove/refactor those modules during unrelated reliability work.

---

## 6. Notification bridge and persistent queue

The Android notification bridge is considered operational. Field diagnostics have repeatedly shown:

```text
configured: true
listener_active: true
last_event_matched: true
last_camera_mapped: true
last_event_queued: true
duplicate_rejected: false
```

Base automatic worker policy:

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

Automatic behavior:

- newest explicitly activated fresh Android event wins;
- stale backlog stays persistent but does not automatically wake the camera;
- events exhausting retries are persistently deferred;
- deferred events are not automatically rearmed;
- startup recovery selects at most the newest fresh non-deferred event;
- other fresh startup events may be deferred as `startup_not_selected`;
- stale startup events are deferred rather than waking the camera;
- newer notification may preempt an older event's retry wait;
- an active camera/download attempt is not cancelled merely because newer motion arrives.

Recording dedupe policy:

```text
persistent_candidate_fingerprint_before_cmd13
```

If multiple queue events map to the same already verified SD-card recording, the later event can complete silently before cmd13/cmd8 rather than re-download/send it.

---

## 7. v1.3.8 — explicit remote-close incomplete stream recovery

Real field failure:

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

v1.3.8 added:

```text
STREAM_REMOTE_DISCONNECT_INCOMPLETE
```

Eligibility:

```text
remote_disconnect_observed == true
termination_reason == connection_closed
file_bytes_written > 0
expected_size > file_bytes_written
```

After this signature occurs, remaining bounded retries use 3 and 6 seconds rather than 30/60 seconds to reuse the battery-camera awake window.

---

## 8. v1.3.9 — partial idle-timeout recovery

Another real field run produced:

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

Eligibility:

```text
file_bytes_written > 0
expected_size > file_bytes_written
termination_reason == idle_timeout
```

It uses the same 3/6-second bounded fast recovery. Zero-byte/unrelated failures retain the ordinary 30/60 policy.

During that field session newer-motion retry preemption was also physically observed:

```text
retry_preemptions: 1
last_preempted_event_time:  2026-08-25T14:18:28.832000+00:00
last_preempting_event_time: 2026-08-25T14:20:28.885000+00:00
```

That preemption behavior is intentional.

---

## 9. v1.3.10 — recording priority over Live View

After v1.3.9, diagnostics showed a fresh recording attempt nominally running for more than 17 minutes while **no UID discovery datagram had been sent**.

Field event:

```text
notification UTC: 2026-08-25T15:53:54.887000+00:00
attempt UTC:      2026-08-25T15:54:54.887984+00:00
```

Worker signature approximately 17 minutes later:

```text
running: true
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

At the same time Live View showed:

```text
active: true
video_consumers: 1
sessions_started: 1
sessions_completed: 0
```

Code inspection confirmed Live View and recording intentionally use the same `runtime.local_operation_lock`, but Live View held it for the entire long-running stream. Recording therefore starved **before UID discovery**.

v1.3.10 added recording priority:

1. recording asks `live_hub.async_pause_for_recording()`;
2. active Live View camera producer receives stop;
3. Live View producer releases the shared camera lease;
4. new/reconnecting Live View producers are gated while recording priority is active;
5. recording obtains the existing shared lock normally;
6. after the attempt `async_resume_after_recording()` releases priority;
7. only one local camera session still exists at a time.

Diagnostics added:

```text
recording_priority_active
recording_priority_depth
recording_preemptions
```

---

## 10. v1.3.11 — preserve Live View consumers across recording priority

After v1.3.10 the field report was:

```text
"גם הלייב פייד הפסיק לעבוד"
Live feed also stopped working.
```

Review found a concrete lifecycle regression: the Live View camera producer correctly yielded for recording, but its common `_finish_producer()` path also published `None` to every H264/AAC queue. The HTTP media handlers interpret `None` as EOF, so go2rtc's source HTTP connections were deliberately terminated during recording preemption.

v1.3.11 separated:

```text
camera producer lifecycle
!=
HTTP/go2rtc consumer lifecycle
```

During recording priority:

- camera producer stops and releases the local operation lock;
- H264/AAC HTTP/go2rtc consumers remain subscribed;
- no intentional EOF is sent to those queues;
- recording runs with priority;
- after recording a fresh camera producer starts against the preserved consumers.

Normal non-recording Live View termination still emits EOF normally.

Diagnostics added:

```text
recording_preserved_finishes
recording_preserved_consumers
```

Expected physical behavior: the image may temporarily freeze/pause while recording owns the camera, but it should resume rather than remain permanently dead.

---

## 11. Why v1.3.12 was requested

The user asked for two usability/recovery controls after discussing restart behavior:

1. a Home Assistant sensor showing how many recordings are still queued;
2. a manual button to explicitly attempt downloading missing queued recordings.

The key design requirement is to preserve the battery-safe automatic policy:

```text
Restart alone must NOT turn an old backlog into repeated camera wakes.
```

But a deliberate user press may safely say:

```text
I explicitly want the integration to try this old backlog now.
```

---

## 12. v1.3.12 — queued recordings sensor

New Home Assistant sensor:

```text
translation key: recordings_queued
English: Recordings queued
Hebrew: סרטונים בתור
unique id suffix: _recordings_queued
icon: mdi:video-outline
```

The native value counts only pending events whose source is:

```text
android_notification
```

It does not count unrelated cloud events because only Android notification events are part of the current automatic recording workflow.

The sensor only appears when the notification bridge/recording worker is configured.

The sensor reads only the in-memory persistent queue and **never contacts the camera**.

Additional attributes:

```text
deferred
fresh_automatic
stale
```

Definitions:

- `deferred`: pending Android events currently excluded from normal automatic processing;
- `fresh_automatic`: pending Android events that are non-deferred and still inside the normal automatic freshness window;
- `stale`: pending Android events older than the automatic freshness window.

The sensor is polling from Home Assistant's in-memory state only; polling this entity does not wake/contact the Argus or cloud.

---

## 13. v1.3.12 — explicit missing-recordings recovery button

New Home Assistant button:

```text
translation key: recover_pending_recordings
English: Download missing recordings
Hebrew: הורד סרטונים חסרים
unique id suffix: _recover_pending_recordings
icon: mdi:download-multiple
```

The button only appears when the notification bridge/recording worker is configured and is available only while at least one pending Android notification event exists.

### Exact semantics on button press

The button calls:

```text
recording_worker.async_request_manual_recovery()
```

That method:

1. takes a snapshot of all currently pending `android_notification` events;
2. explicitly rearms any of those events that are deferred;
3. adds every current pending Android event ID to a dedicated manual-recovery set;
4. also activates those IDs in the serialized worker;
5. signals the worker trigger;
6. does **not** directly open a camera session inside the button handler.

### What manual mode changes

For event selection, a manual-recovery event is eligible even if it is older than the normal automatic 10-minute window.

The stale-event deferral pass deliberately skips IDs in the manual-recovery set.

This is the only freshness bypass.

### What manual mode does NOT change

Manual backlog recovery still uses the normal worker pipeline:

```text
settle calculation
-> recording priority over Live View
-> UID discovery
-> local auth
-> FileInfo lookup
-> cmd13
-> cmd8
-> exact-size verification
-> persistent completed fingerprint
-> ready event
```

It still retains:

- max 3 attempts per event;
- normal 30/60 retry policy for ordinary failures;
- v1.3.8/v1.3.9 3/6-second fast recovery after proven partial stream failure;
- newer-motion retry preemption;
- Live View recording-priority arbitration;
- exact MP4 verification;
- partial-file cleanup;
- completed-recording dedupe;
- persistent event completion only after verified success/dedupe.

If manual work fails all attempts it is deferred again and removed from the manual-recovery set.

If manual work succeeds or is deduplicated, it is removed from the manual-recovery set and from the persistent pending queue through the existing completion path.

### Manual recovery ordering

Current selector remains newest-first across eligible explicit/manual and normal fresh work. This preserves the existing policy that newly arriving real motion should not wait behind very old backlog.

After a newer event is handled, remaining manual backlog stays eligible and continues through the serialized worker.

---

## 14. New v1.3.12 diagnostics

`recording_worker.policy_diagnostics()` now adds:

```text
manual_recovery_requests
manual_recovery_last_queued
manual_recovery_rearmed
manual_recovery_remaining
manual_recovery_policy = explicit_button_all_pending
```

Interpretation:

- `manual_recovery_requests`: number of explicit recovery button requests in the current runtime;
- `manual_recovery_last_queued`: count of Android pending events selected by the last button press;
- `manual_recovery_rearmed`: cumulative number of deferred events rearmed by manual requests;
- `manual_recovery_remaining`: number of events from the manual-recovery set that still remain pending;
- `manual_recovery_policy`: confirms that only the explicit all-pending button path bypasses automatic freshness.

Existing `deferred_rearmed_count` is also incremented when the manual button rearms deferred events.

---

## 15. Important restart behavior after v1.3.12

v1.3.12 **does not** change automatic startup recovery.

After Home Assistant restart:

- at most the newest fresh non-deferred Android event is automatically selected;
- stale events are not automatically downloaded;
- deferred events are not automatically downloaded;
- old backlog therefore does not repeatedly wake a battery camera after every restart.

The new button is the explicit escape hatch for that backlog.

Example:

```text
sensor: סרטונים בתור = 17
attributes:
  deferred: 17
  fresh_automatic: 0
  stale: 17
```

Restart alone:

```text
no automatic old-backlog wake
```

User presses:

```text
הורד סרטונים חסרים
```

Then:

```text
all 17 current Android pending events are explicitly marked for recovery
-> deferred entries are rearmed
-> worker begins serialized recovery
```

The user should expect battery usage during this explicitly requested bulk recovery.

---

## 16. Files changed for v1.3.12

Worker/manual recovery:

```text
custom_components/reolink_battery/recording_worker_v138.py
```

Home Assistant entities:

```text
custom_components/reolink_battery/sensor.py
custom_components/reolink_battery/button.py
```

Translations:

```text
custom_components/reolink_battery/strings.json
custom_components/reolink_battery/translations/en.json
custom_components/reolink_battery/translations/he.json
```

Regression coverage:

```text
tests/test_manual_recovery_controls.py
.github/workflows/validate.yaml
```

Release docs:

```text
custom_components/reolink_battery/manifest.json
CHANGELOG.md
CHECKPOINT.md
docs/checkpoints/v1.3.12.md
```

---

## 17. Regression coverage added in v1.3.12

`tests/test_manual_recovery_controls.py` verifies source-level policy contracts including:

- explicit `async_request_manual_recovery()` exists;
- deferred events are rearmed through coordinator API;
- all current Android pending IDs are added to manual recovery;
- manual events bypass freshness;
- ordinary activated automatic events still require freshness;
- stale automatic deferral excludes explicit manual recovery IDs;
- completed/failed manual work leaves the manual set;
- queue sensor counts Android events only;
- queue attributes exist;
- recovery button invokes the worker recovery API;
- Hebrew labels `סרטונים בתור` and `הורד סרטונים חסרים` exist.

The normal validation workflow also still runs:

```text
compileall
release checkpoint consistency
notification bridge regression tests
recording backlog policy regression tests
incomplete stream recovery regression tests
recording vs Live View priority regression tests
manual recovery controls regression tests
integration load/startup regression tests
HACS validation
```

Before the version bump, all Python/regression/integration-load tests were green on the feature branch after compatibility cleanup.

---

## 18. What v1.3.12 deliberately does NOT change

Do not attribute future recording-transfer behavior to the queue UI/recovery patch unless diagnostics specifically indicate manual selection issues.

v1.3.12 does not intentionally change:

- notification matching/fingerprint logic;
- 60-second automatic settle;
- automatic 600-second freshness window;
- normal startup recovery rules;
- max three attempts;
- normal 30/60 retry delays;
- partial-stream 3/6 retry delays;
- cmd13 wire format;
- cmd8 wire format;
- FileInfo query shape;
- full-high/mainStream selection;
- heartbeat behavior;
- UDP ACK cadence/bitmap behavior;
- authoritative collector sizing;
- exact-size acceptance;
- partial cleanup;
- persistent dedupe;
- Live View consumer-preservation fix from v1.3.11;
- single-local-session invariant.

---

## 19. Next physical validation after installing v1.3.12

### A. Basic entity check

After HACS update and HA restart confirm diagnostics show:

```text
version: 1.3.12
```

With notification bridge configured, confirm the device exposes:

```text
sensor ... סרטונים בתור
button ... הורד סרטונים חסרים
```

The sensor value should approximately match the pending Android backlog previously visible in diagnostics.

### B. Inspect queue attributes

Check:

```text
deferred
fresh_automatic
stale
```

For the previously accumulated old backlog, most/all may be `deferred` and `stale`.

### C. Manual recovery test

Prefer testing first when Live View is not intentionally open, to isolate queue recovery behavior.

1. Note the queued sensor value.
2. Press `הורד סרטונים חסרים` once.
3. Do not repeatedly press the button while the same recovery is already active.
4. Allow the serialized worker to process.
5. Export diagnostics after one or more events complete/fail.

Expected manual diagnostics:

```text
manual_recovery_requests >= 1
manual_recovery_last_queued > 0
manual_recovery_rearmed >= 0
manual_recovery_remaining <= manual_recovery_last_queued
manual_recovery_policy = explicit_button_all_pending
```

If deferred backlog existed before the press:

```text
manual_recovery_rearmed > 0
```

The queued sensor should decrease as events successfully complete or dedupe.

### D. Recording transport success target

For a successfully recovered event:

```text
file_saved: true
final_size_match: true
atomic_rename_completed: true
recording_worker.last_file_saved: true
recording_worker.last_ready_event_fired: true
```

If the queued event maps to an already completed recording, it may disappear through dedupe without a second cmd13/cmd8 or Telegram send. That is correct.

### E. If a manual event still fails

Inspect:

```text
recording_worker.last_failure_stage
recording_worker.last_failure_type
recording_worker.prior_attempt.*
recording_worker.uid_resolve.*
download_prepare.stream_probe.*
```

If real media bytes start and end with known signatures, expect existing classifiers:

```text
STREAM_REMOTE_DISCONNECT_INCOMPLETE
STREAM_IDLE_TIMEOUT_INCOMPLETE
```

Do not redesign queue recovery because of a transport-layer partial stream; analyze the media transport evidence separately.

### F. Combined Live View test after basic recovery validation

After confirming the button works, repeat with Live View open:

Expected if recording starts while Live View owns the producer:

```text
recording_preemptions >= 1
recording_preserved_finishes >= 1
recording_preserved_consumers >= 1
```

After the recording attempt:

```text
recording_priority_active: false
recording_priority_depth: 0
```

The feed may pause but should resume.

---

## 20. Diagnostics fields most useful next

Version:

```text
custom_components.reolink_battery.version
integration_manifest.version
```

Queue/event state:

```text
events.pending_count
events.completed_recording_count
recording_worker.deferred_count
recording_worker.persistent_deferred_count
recording_worker.eligible_fresh_pending_count
recording_worker.stale_pending_count
recording_worker.manual_recovery_requests
recording_worker.manual_recovery_last_queued
recording_worker.manual_recovery_rearmed
recording_worker.manual_recovery_remaining
recording_worker.manual_recovery_policy
```

Worker:

```text
recording_worker.running
recording_worker.pending_trigger
recording_worker.waiting_camera_closed
recording_worker.attempts
recording_worker.retries
recording_worker.completed
recording_worker.last_event_time
recording_worker.last_attempt_time
recording_worker.last_failure_stage
recording_worker.last_failure_type
recording_worker.last_file_saved
recording_worker.last_ready_event_fired
recording_worker.prior_attempt.*
```

UID wake:

```text
recording_worker.uid_resolve.send_rounds
recording_worker.uid_resolve.datagrams_sent
recording_worker.uid_resolve.elapsed_ms
recording_worker.uid_resolve.succeeded
```

Stream:

```text
download_prepare.stream_probe.cmd13_frames
download_prepare.stream_probe.cmd8_frames
download_prepare.stream_probe.file_bytes_written
download_prepare.stream_probe.xml_reported_size
download_prepare.stream_probe.final_size_match
download_prepare.stream_probe.file_saved
download_prepare.stream_probe.termination_reason
download_prepare.stream_probe.remote_disconnect_observed
download_prepare.stream_probe.cmd13_udp_ack_received
download_prepare.stream_probe.cmd8_udp_ack_received
download_prepare.stream_probe.udp_current_missing_packet_count_at_disconnect
download_prepare.stream_probe.udp_max_gap_recovery_ms
```

Live View arbitration:

```text
live_view.session.active
live_view.session.video_consumers
live_view.session.recording_priority_active
live_view.session.recording_priority_depth
live_view.session.recording_preemptions
live_view.session.recording_preserved_finishes
live_view.session.recording_preserved_consumers
live_view.session.last_failure_stage
live_view.session.last_failure_type
live_view.session.last_session
```

---

## 21. Important interpretation rules

### A. Queue count is event count, not guaranteed unique physical video count

The new sensor counts pending Android recording events. Several notification events can potentially map to the same physical SD clip. Persistent recording fingerprint dedupe resolves that during processing.

Therefore:

```text
queued sensor = pending recovery events
```

not necessarily:

```text
unique SD MP4 files remaining
```

### B. Manual recovery is intentionally battery-expensive compared with normal policy

Pressing the button explicitly asks the integration to process old backlog. A large queued count can therefore cause multiple camera wake/download attempts. This is user-initiated behavior, not automatic background behavior.

### C. Do not repeatedly press the recovery button to make it faster

A second press will reselect the current pending snapshot and increment diagnostics, but the worker remains serialized. Repeated presses do not create parallel camera sessions.

### D. `running: true` does not prove network activity

Always correlate worker running state with UID/stream telemetry. v1.3.9 field evidence proved a worker can be blocked before UID discovery if a shared lease is not available; v1.3.10 specifically addressed the known Live View version of that problem.

### E. Current stream probe may reset on a newer attempt

Use `recording_worker.prior_attempt` when diagnostics are captured mid-run after a previous attempt has already failed.

### F. Historical UDP unresolved count is not necessarily current final loss

Prefer current-at-disconnect fields such as:

```text
udp_current_missing_packet_count_at_disconnect
udp_expected_next_seq_at_disconnect
udp_highest_network_seq_seen
udp_buffered_out_of_order_at_disconnect
udp_max_gap_recovery_ms
```

### G. Valid MP4 prefix is not enough

A `.part` containing a valid `ftyp` is still rejected unless final byte count exactly matches authoritative expected size.

---

## 22. Source files that matter most

Integration/runtime:

```text
custom_components/reolink_battery/__init__.py
custom_components/reolink_battery/diagnostics.py
custom_components/reolink_battery/coordinator.py
```

Notification/queue:

```text
custom_components/reolink_battery/notification_bridge.py
custom_components/reolink_battery/events.py
```

Worker/policy:

```text
custom_components/reolink_battery/recording_worker.py
custom_components/reolink_battery/recording_worker_v138.py
```

Recording transport:

```text
custom_components/reolink_battery/recording_probe.py
custom_components/reolink_battery/recording_download_probe.py
custom_components/reolink_battery/recording_download_probe_beta20.py
custom_components/reolink_battery/recording_download_probe_beta21.py
custom_components/reolink_battery/recording_download_beta22.py
```

Live View:

```text
custom_components/reolink_battery/live_http.py
custom_components/reolink_battery/live_stream.py
custom_components/reolink_battery/go2rtc_bridge.py
custom_components/reolink_battery/camera.py
```

New v1.3.12 controls:

```text
custom_components/reolink_battery/sensor.py
custom_components/reolink_battery/button.py
```

Tests:

```text
tests/test_notification_bridge.py
tests/test_recording_backlog_policy.py
tests/test_recording_stream_recovery_v138.py
tests/test_recording_live_priority.py
tests/test_manual_recovery_controls.py
tests/test_integration_load.py
tests/test_release_checkpoint.py
```

---

## 23. New-chat kickoff prompt

Copy/paste this into a new chat:

```text
Continue development of Dmxsir/ha-reolink-battery.

First read CHECKPOINT.md completely and treat it as the authoritative handoff.
Then inspect current main and the latest diagnostics I provide before changing
recording transport or queue semantics.

Current release/target state is v1.3.12 on Reolink Argus 2E.
The recording transport is already physically proven: UID/legacy auth, FileInfo,
cmd13 handle, full-high/mainStream cmd8, ~1s heartbeat, periodic inclusive-highest
UDP ACK, exact-size MP4 verification and end-to-end Telegram have worked.

v1.3.8: fast recovery for partial stream + connection_closed.
v1.3.9: fast recovery for partial stream + idle_timeout.
v1.3.10: recording priority prevents indefinitely active Live View from starving
recording before UID discovery.
v1.3.11: recording preemption preserves H264/AAC HTTP/go2rtc consumers so Live
View can resume after recording instead of receiving intentional EOF.
v1.3.12: adds a queued-recordings sensor and an explicit Download missing
recordings button. Automatic 10-minute/startup battery policy remains unchanged;
only explicit button recovery rearms/bypasses freshness for current pending Android
events, and those events still use the full verified worker path.

Do not rewrite cmd13/cmd8/heartbeat/UDP ACK or change multiple timing variables at
once without new physical evidence.

For every future version update, update CHECKPOINT.md and create the matching
byte-identical docs/checkpoints/v<version>.md before release.
```

---

## 24. Handoff summary

```text
Android notification ingestion                   working
persistent queue / stale backlog policy          working
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
Live View HTTP consumer preservation             implemented v1.3.11
queued recordings sensor                         implemented v1.3.12
manual all-pending backlog recovery button       implemented v1.3.12
automatic startup freshness policy               unchanged / battery-safe
v1.3.12 physical queue-recovery validation       NEXT REQUIRED TEST
```

**Next action:** install/confirm v1.3.12 in Home Assistant, verify `סרטונים בתור` and `הורד סרטונים חסרים`, note the queue attributes, press recovery once, then send diagnostics after the worker processes at least one old/deferred event. Do not alter recording transport again before examining that field result.
