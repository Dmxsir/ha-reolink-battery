# Reolink Battery Development Checkpoint

**Checkpoint version:** v1.3.13  
**Checkpoint date:** 2026-08-28  
**Repository:** `Dmxsir/ha-reolink-battery`  
**Release target:** `v1.3.13`  
**Primary field-test camera:** Reolink Argus 2E  
**Home Assistant field environment:** HA 2026.8.2 / HA OS 18.2 / Python 3.14.6 / x86_64  
**Camera firmware observed:** `0616_722_52_478`  
**Camera hardware observed:** `BIPC_36S7616MC94`

---

## 1. Purpose and release policy

This is the authoritative handoff for continuing development of `Dmxsir/ha-reolink-battery`.

The Argus 2E recording transport is physically proven. Current work is reliability hardening, battery-safe queue policy and Home Assistant usability. Do not rediscover or rewrite cmd13/cmd8 because a worker/UI/queue test fails.

Every future version bump must:

1. update root `CHECKPOINT.md`;
2. create a byte-identical archive at `docs/checkpoints/v<version>.md`;
3. update `CHANGELOG.md` and `custom_components/reolink_battery/manifest.json`;
4. pass `tests/test_release_checkpoint.py`;
5. pass compile, regression, integration-load and HACS validation;
6. only then merge/release.

The release workflow verifies checkpoint consistency before publication.

---

## 2. Product goal

Battery-safe automatic recording flow:

```text
Reolink motion
-> official Reolink Android notification
-> Home Assistant Companion Last Notification sensor
-> notification bridge
-> persistent event queue
-> 60-second recording settle while camera remains closed
-> UID wake/discovery
-> legacy local authentication
-> FileInfo recording lookup
-> cmd13 prepare/download handle
-> cmd8 full MP4 transfer
-> exact authoritative-size verification
-> fsync + atomic .part -> .mp4
-> reolink_battery_recording_ready
-> optional Home Assistant/Telegram automation
```

On-demand Live View is separate through the integration/go2rtc. Automatic recording does not require continuous RTSP/ONVIF/polling.

---

## 3. Battery-safety invariants

Preserve these unless later physical evidence deliberately changes them:

1. No continuous camera polling for automatic motion detection.
2. Android push notification is the automatic trigger.
3. Automatic recording settle remains 60 seconds.
4. Only one local Baichuan camera session at a time.
5. Recording has priority over an indefinitely open Live View once an attempt is ready.
6. Ordinary automatic retries remain bounded to three attempts per event.
7. Stale backlog does not wake the camera merely because Home Assistant restarted.
8. Deferred backlog is not silently rearmed by unrelated new motion.
9. Only the explicit manual recovery button may bypass the automatic freshness rule.
10. Partial MP4 files never become final recordings.
11. Exact authoritative expected size is required before final save/ready event.
12. Completed-recording fingerprints prevent duplicate downloads/sends of the same physical SD clip.

---

## 4. Authentication and tested camera

```text
Model: Argus 2E
Firmware: 0616_722_52_478
Hardware: BIPC_36S7616MC94
Auth path: legacy_local_credential
```

Established conclusions:

- Reolink cloud auth works.
- The camera is bound to the account.
- Cloud credentials alone are insufficient for local download.
- A local camera administrator password is required.
- The tested unit does not expose a usable protocol-3/sigV3 path.
- UID discovery/wake and legacy local auth work on the physical camera.
- Battery wake/readiness races can still create transient UID/auth/FileInfo failures.

Current requirements:

```text
pycryptodomex==3.23.0
reolink-aio==0.21.8
```

---

## 5. Physically proven recording transport — do not rewrite speculatively

Official Windows SDK oracle on the same Argus 2E showed:

```text
LAN -> OPEN_SUCCESS
recording query -> response code 0
cmd16 before download
cmd13 accepted
cmd8 accepted
10,521,742 / 10,521,742 bytes received
```

The portable implementation later reproduced full end-to-end downloads and Telegram delivery.

Validated transport behavior:

- FileInfo lookup and candidate selection;
- cmd13 class/routing and returned handle;
- cmd8 full-high `FileInfo` layout;
- forced `mainStream` on the tested unit;
- same authenticated session handoff;
- fresh post-login heartbeat transaction IDs;
- P2P heartbeat about every 1 second;
- periodic UDP ACK about every 10 ms;
- inclusive-highest ACK semantics;
- authoritative expected recording size;
- `.part` temporary file;
- MP4 `ftyp` validation;
- fsync;
- exact-size verification;
- atomic rename only on exact success;
- partial cleanup on failure;
- ready event only after verified final MP4.

Retained reference releases:

```text
v0.1.2-beta.40 — heartbeat/ACK/cmd13/cmd8/full-high/mainStream baseline
v0.1.2-beta.45 — authoritative cmd13 size, >16 MiB verified downloads, 128 MiB hard cap
```

Stable code intentionally still inherits beta-named transport modules. Do not remove/refactor them during unrelated reliability work.

---

## 6. Base automatic queue/worker policy

```text
RECORDING_SETTLE_SECONDS = 60.0
MAX_ATTEMPTS_PER_TRIGGER = 3
RETRY_DELAYS_SECONDS = (30.0, 60.0)
AUTOMATIC_EVENT_MAX_AGE_SECONDS = 600
selection_policy = newest_activated_fresh_first
deferred_rearm_policy = explicit_only
startup_recovery_policy = newest_fresh_non_deferred_only
retry_preemption_policy = newer_notification_before_retry
recording_dedupe_policy = persistent_candidate_fingerprint_before_cmd13
```

Behavior:

- newest explicitly activated fresh Android event wins;
- stale backlog persists but does not automatically wake the camera;
- events exhausting retries are persistently deferred;
- restart selects at most the newest fresh non-deferred event;
- newer motion may preempt an older event's retry wait;
- an active camera/download attempt is not cancelled by newer motion;
- multiple notification events mapping to one already verified SD recording may complete silently through fingerprint dedupe before cmd13/cmd8.

---

## 7. Reliability history retained

### v1.3.8 — incomplete stream + explicit remote close

Real field signature:

```text
expected: 9,555,011
written: 3,342,336
ftyp valid: true
termination: connection_closed
remote disconnect: true
```

Added `STREAM_REMOTE_DISCONNECT_INCOMPLETE` and 3/6-second remaining retries after a proven partial transfer.

### v1.3.9 — incomplete stream + idle timeout

Real field signature:

```text
expected: 9,314,178
written: 1,261,568
termination: idle_timeout
remote disconnect: false
```

Added `STREAM_IDLE_TIMEOUT_INCOMPLETE` with the same 3/6-second recovery. Zero-byte/wake/auth/FileInfo failures retain ordinary timing.

### v1.3.10 — recording priority over Live View

A fresh recording remained blocked for more than 17 minutes before UID discovery because Live View held the shared `local_operation_lock`.

Added recording-priority arbitration so Live View yields its producer before the worker takes the existing lock. Single-local-session invariant remains.

### v1.3.11 — preserve Live View consumers

The first priority implementation ended H264/AAC HTTP consumers with EOF. v1.3.11 preserves consumer queues during recording priority and restarts the camera producer afterward. Live View may freeze during recording but should resume rather than remain dead.

### v1.3.12 — queue visibility and explicit backlog recovery

Added:

```text
sensor: Recordings queued / סרטונים בתור
button: Download missing recordings / הורד סרטונים חסרים
```

The button explicitly rearms all current pending Android events and marks them as manual recovery work. Manual work may bypass the 10-minute freshness limit, but still uses the verified serialized worker, Live View priority, exact-size verification, dedupe and bounded retry logic.

Automatic restart behavior remained unchanged.

---

## 8. v1.3.12 physical field validation — successful path

The first v1.3.12 field run validated the user-facing queue workflow.

Observed by the user:

```text
queue before download: 18
verified MP4 downloaded
Home Assistant automation sent MP4 to Telegram
queue after success: 17
next successful recording: 16
```

This proves:

- queued-recordings sensor decrements when pending events complete;
- verified download path still works after queue/manual changes;
- `reolink_battery_recording_ready` still drives the Telegram automation end-to-end.

---

## 9. v1.3.12 physical field validation — backlog retry cost

Diagnostics captured 2026-08-26 after manual backlog recovery showed:

```text
pending_count: 16
recording_worker.attempts: 52
recording_worker.retries: 34
recording_worker.completed: 2
recording_worker.deferred_count: 16
manual_recovery_requests: 2
manual_recovery_last_queued: 17
manual_recovery_rearmed: 18
manual_recovery_remaining: 0
last_failure_stage: RECORDING_MATCH_ERROR
last_failure_type: DownloadPrepareError
```

The arithmetic was exact:

```text
18 initial event attempts + 34 retries = 52 attempts
```

The manual recovery was therefore not idle for half an hour. It was repeatedly spending normal 30/60-second retry windows on old events whose FileInfo lookup could not map the historical notification to an SD recording.

Key conclusion:

```text
old explicit manual event + RECORDING_MATCH_ERROR
```

is fundamentally different from a transient wake/UID/auth failure. For a historical event already far beyond settle/freshness, waiting another 30/60 seconds cannot cause a missing historical FileInfo match to appear.

---

## 10. 2026-08-28 field evidence — transient UID failures still need retries

Later diagnostics showed substantial backlog progress:

```text
events.pending_count: 10
events.completed_recording_count: 29
recording_worker.attempts: 106
recording_worker.retries: 56
recording_worker.completed: 20
recording_worker.deduplicated_recordings: 3
recording_worker.deferred_count: 9
eligible_fresh_pending_count: 1
stale_pending_count: 8
manual_recovery_requests: 3
manual_recovery_last_queued: 16
manual_recovery_rearmed: 34
manual_recovery_remaining: 0
```

Newest-motion retry preemption was also physically observed again:

```text
last_preempted_event_time:  2026-08-28T11:32:46.127000+00:00
last_preempting_event_time: 2026-08-28T11:34:52.363000+00:00
retry_preemptions: 1
```

The current failure at capture time was different:

```text
last_failure_stage: UID_RESOLVE_ERROR
last_failure_type: TimeoutError
uid timeout: 15.0 s
send_rounds: 25
datagrams_sent: 100
elapsed_ms: ~15029.653
succeeded: false
```

This failed before auth/FileInfo/cmd13/cmd8. It is a transient camera wake/reachability class and must retain normal retry opportunities.

Therefore v1.3.13 must **not** globally make manual recovery single-attempt.

---

## 11. v1.3.13 decision — failure-aware manual retry policy

Implemented in:

```text
custom_components/reolink_battery/recording_worker_v138.py
```

New policy:

```text
IF event is explicitly in manual recovery
AND event is stale under the normal automatic freshness rule
AND last_failure_stage == RECORDING_MATCH_ERROR
THEN do not wait 30/60 seconds for another match attempt
     defer immediately after that one attempt
ELSE keep existing retry behavior
```

Specific defer reason:

```text
manual_stale_recording_match_miss
```

Diagnostics added:

```text
manual_stale_match_single_attempts
manual_stale_match_retry_policy = single_attempt_then_defer
```

Important boundaries:

- fresh manual events still keep normal retries because the recording may still be finalizing;
- `UID_RESOLVE_ERROR` still keeps normal retries;
- auth/wake/transient worker errors still keep normal retries;
- proven partial transfers still use v1.3.8/v1.3.9 3/6-second fast recovery;
- automatic events keep the existing max-three-attempt policy;
- no transport code was changed.

---

## 12. v1.3.13 queue visibility

Existing sensor retained:

```text
Recordings queued / סרטונים בתור
```

New separate sensors:

```text
Recordings deferred / סרטונים שנדחו
Recovery remaining / נותרו בשחזור
```

The queued sensor also exposes:

```text
deferred
fresh_automatic
stale
recovery_remaining
worker_running
waiting_camera_closed
```

All three queue sensors inspect only in-memory coordinator/worker state. They do not contact the Reolink cloud and do not wake/contact the camera.

Interpretation:

- `Recordings queued`: all pending Android notification events, not guaranteed unique SD files;
- `Recordings deferred`: pending Android events currently excluded from normal automatic work;
- `Recovery remaining`: explicit manual-recovery IDs that remain pending in the current runtime.

A deferred event still remains part of the pending queue until later success/dedupe/removal, so `queued` and `deferred` may be equal.

---

## 13. Files changed for v1.3.13

```text
custom_components/reolink_battery/recording_worker_v138.py
custom_components/reolink_battery/sensor.py
custom_components/reolink_battery/strings.json
custom_components/reolink_battery/translations/en.json
custom_components/reolink_battery/translations/he.json
tests/test_manual_recovery_controls.py
custom_components/reolink_battery/manifest.json
CHANGELOG.md
CHECKPOINT.md
docs/checkpoints/v1.3.13.md
```

No cmd13/cmd8/heartbeat/UDP ACK/recording transport module is intentionally changed.

---

## 14. Regression requirements for v1.3.13

`tests/test_manual_recovery_controls.py` must verify:

- explicit all-pending recovery still exists;
- manual events still bypass freshness only when explicitly selected;
- stale automatic work is still deferred normally;
- old manual `RECORDING_MATCH_ERROR` is recognized as terminal for that recovery pass;
- UID/auth transient stages are not part of that terminal classifier;
- terminal stale-match events leave the manual set through normal deferral;
- queued sensor still counts Android events only;
- queued attributes include `deferred`, `fresh_automatic`, `stale`, `recovery_remaining`, `worker_running`, `waiting_camera_closed`;
- separate deferred and recovery-remaining sensors exist;
- Hebrew labels exist;
- recovery button still calls `async_request_manual_recovery()`.

Normal CI must also retain:

```text
compileall
release checkpoint consistency
notification bridge tests
recording backlog tests
incomplete stream recovery tests
recording vs Live View tests
manual recovery controls tests
integration load/startup tests
HACS validation
```

---

## 15. What v1.3.13 deliberately does NOT change

Do not attribute future transport failures to this patch unless diagnostics show a queue/policy problem.

Unchanged:

- notification matching/fingerprint logic;
- 60-second automatic settle;
- 600-second automatic freshness window;
- startup recovery rules;
- automatic max-three-attempt bound;
- normal 30/60 retry delays for ordinary retryable failures;
- partial-stream 3/6 retry delays;
- newest-motion retry preemption;
- cmd13 wire format;
- cmd8 wire format;
- FileInfo query shape/candidate selection logic;
- full-high/mainStream selection;
- heartbeat behavior;
- UDP ACK cadence/bitmap behavior;
- authoritative collector sizing;
- exact-size acceptance;
- partial cleanup;
- persistent recording dedupe;
- Live View consumer preservation;
- single-local-session invariant.

---

## 16. Next physical validation after installing v1.3.13

1. Update through HACS and restart Home Assistant.
2. Confirm diagnostics version is `1.3.13`.
3. Confirm device exposes:

```text
סרטונים בתור
סרטונים שנדחו
נותרו בשחזור
הורד סרטונים חסרים
```

4. Before pressing recovery, note all three sensor values.
5. Press `הורד סרטונים חסרים` once only.
6. If an old backlog event fails with `RECORDING_MATCH_ERROR`, expected behavior is:

```text
one attempt
-> immediate defer
-> next event
```

not:

```text
attempt
-> 30 s
-> attempt
-> 60 s
-> attempt
```

7. Expected diagnostics for at least one old match miss:

```text
manual_stale_match_single_attempts >= 1
manual_stale_match_retry_policy = single_attempt_then_defer
last_deferred_reason = manual_stale_recording_match_miss
```

8. If an event instead fails with `UID_RESOLVE_ERROR`, retries should still occur. That is intentional.
9. Successful recovered events must still show exact verified save and continue to Telegram through the existing automation.

---

## 17. Useful diagnostics for the next field result

Queue/policy:

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
recording_worker.manual_stale_match_single_attempts
recording_worker.manual_stale_match_retry_policy
recording_worker.last_deferred_reason
```

Worker:

```text
recording_worker.running
recording_worker.waiting_camera_closed
recording_worker.attempts
recording_worker.retries
recording_worker.completed
recording_worker.deduplicated_recordings
recording_worker.last_event_time
recording_worker.last_attempt_time
recording_worker.last_failure_stage
recording_worker.last_failure_type
recording_worker.prior_attempt.*
```

UID:

```text
recording_worker.uid_resolve.send_rounds
recording_worker.uid_resolve.datagrams_sent
recording_worker.uid_resolve.elapsed_ms
recording_worker.uid_resolve.succeeded
```

Verified stream success:

```text
download_prepare.stream_probe.file_bytes_written
download_prepare.stream_probe.xml_reported_size
download_prepare.stream_probe.final_size_match
download_prepare.stream_probe.atomic_rename_completed
download_prepare.stream_probe.file_saved
recording_worker.last_file_saved
recording_worker.last_ready_event_fired
```

Live View arbitration:

```text
live_view.session.recording_priority_active
live_view.session.recording_priority_depth
live_view.session.recording_preemptions
live_view.session.recording_preserved_finishes
live_view.session.recording_preserved_consumers
```

---

## 18. Known caution retained

`async_pause_for_recording()` currently increments recording priority before awaiting producer shutdown. If future diagnostics ever show `recording_priority_depth > 0` with no recording in progress, inspect cancellation during pause and harden with a context-manager/paused-flag approach. Do not change this absent evidence.

---

## 19. New-chat kickoff

```text
Continue development of Dmxsir/ha-reolink-battery.

Read CHECKPOINT.md completely first and treat it as authoritative.
Current release target is v1.3.13 on Reolink Argus 2E.

The recording transport is physically proven: UID/legacy auth, FileInfo, cmd13
handle, full-high/mainStream cmd8, ~1 s heartbeat, periodic inclusive-highest UDP
ACK, exact-size MP4 verification and Telegram delivery have worked.

v1.3.8 added fast recovery for partial connection_closed.
v1.3.9 added fast recovery for partial idle_timeout.
v1.3.10 added recording priority over Live View.
v1.3.11 preserved Live View HTTP/go2rtc consumers across recording priority.
v1.3.12 added queued-recordings visibility and explicit all-pending manual recovery.
v1.3.13 uses field evidence to make only stale manual RECORDING_MATCH_ERROR events
single-attempt before defer, while UID/auth/transient failures retain retries. It
also adds separate deferred and recovery-remaining sensors.

Do not rewrite cmd13/cmd8/heartbeat/UDP ACK or change unrelated timing without new
physical evidence.

For every future version update, update CHECKPOINT.md and create the matching
byte-identical docs/checkpoints/v<version>.md before release.
```

---

## 20. Handoff summary

```text
Android notification ingestion                    working
persistent queue / stale backlog policy           working
recording fingerprint dedupe                      physically exercised
UID discovery / legacy auth                       physically proven
FileInfo lookup                                   physically proven
cmd13/cmd8 request shape                          physically proven
full exact MP4 download                           physically proven
recording-ready / Telegram end-to-end             physically proven
partial remote-close fast recovery                v1.3.8
partial idle-timeout fast recovery                v1.3.9
recording priority over Live View                 v1.3.10
Live View consumer preservation                   v1.3.11
queued recordings + manual recovery               v1.3.12
manual backlog physical validation                completed
stale manual match-miss retry optimization        v1.3.13
separate deferred/recovery queue sensors           v1.3.13
v1.3.13 physical validation                       NEXT REQUIRED TEST
```

**Next action:** validate v1.3.13 on the physical Argus 2E. Confirm old manual `RECORDING_MATCH_ERROR` events defer after one attempt while `UID_RESOLVE_ERROR` still receives retries, and confirm the three queue counters reflect pending/deferred/current-recovery state without waking the camera.
