# Reolink Battery Development Checkpoint

**Checkpoint version:** v1.3.15  
**Checkpoint date:** 2026-08-28  
**Repository:** `Dmxsir/ha-reolink-battery`  
**Release target:** `v1.3.15`  
**Primary field-test camera:** Reolink Argus 2E  
**Home Assistant field environment:** HA 2026.8.2 / HA OS 18.2 / Python 3.14.6 / x86_64  
**Camera firmware observed:** `0616_722_52_478`  
**Camera hardware observed:** `BIPC_36S7616MC94`

---

## 1. Purpose and release policy

This is the authoritative handoff for continuing development of `Dmxsir/ha-reolink-battery`.

The Argus 2E recording transport is physically proven. Current work is reliability hardening, battery-safe queue policy and Home Assistant usability. Do not rediscover or rewrite cmd13/cmd8 because a queue/UI/worker test fails.

Every future version bump must:

1. update root `CHECKPOINT.md`;
2. create a byte-identical archive at `docs/checkpoints/v<version>.md`;
3. update `CHANGELOG.md` and `custom_components/reolink_battery/manifest.json`;
4. pass `tests/test_release_checkpoint.py`;
5. pass compile, regression, integration-load and HACS validation;
6. only then merge/release.

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

Preserve these unless new physical evidence deliberately changes them:

1. No continuous camera polling for automatic motion detection.
2. Android push notification is the automatic trigger.
3. Automatic recording settle remains 60 seconds.
4. Only one local Baichuan camera session at a time.
5. Recording has priority over an indefinitely open Live View once an attempt is ready.
6. Ordinary automatic retries remain bounded to three attempts per selected event.
7. Stale backlog does not wake the camera merely because Home Assistant restarted.
8. Deferred backlog is not silently rearmed by unrelated new motion.
9. Manual recovery bypasses freshness only after the explicit recovery button.
10. A normal automatic event accepted while fresh must not lose its first worker attempt solely because the serialized worker was busy with already-running work.
11. Partial MP4 files never become final recordings.
12. Exact authoritative expected size is required before final save/ready event.
13. Completed-recording fingerprints prevent duplicate downloads/sends of the same physical SD clip.
14. Clearing dead deferred backlog must be an explicit user action, queue-only, and must never wake/contact the camera or delete verified MP4 files.
15. Cleared historical event IDs remain in processed-event dedupe so an old Android notification cannot be re-ingested as new work.

---

## 4. Authentication and physically proven transport

Tested camera:

```text
Model: Argus 2E
Firmware: 0616_722_52_478
Hardware: BIPC_36S7616MC94
Auth path: legacy_local_credential
```

Established conclusions:

- Reolink cloud authentication works.
- The camera is bound to the account.
- Cloud credentials alone are insufficient for local recording download.
- A local camera administrator password is required.
- UID discovery/wake and legacy local authentication work on the physical camera.
- Battery wake/readiness races can still create transient UID/auth/FileInfo failures.

Official Windows SDK oracle and later portable implementation physically proved:

```text
LAN -> OPEN_SUCCESS
recording query -> response code 0
cmd16 before download
cmd13 accepted
cmd8 accepted
10,521,742 / 10,521,742 bytes received
```

Validated behavior includes FileInfo selection, cmd13 handle, cmd8 full-high/mainStream transfer, fresh heartbeat transaction IDs, approximately 1-second P2P heartbeat, approximately 10-ms periodic UDP ACK, inclusive-highest ACK semantics, authoritative expected size, `.part`, MP4 `ftyp`, fsync, exact-size verification, atomic rename and `reolink_battery_recording_ready`.

Retained references:

```text
v0.1.2-beta.40 — heartbeat/ACK/cmd13/cmd8/full-high/mainStream baseline
v0.1.2-beta.45 — authoritative cmd13 size, >16 MiB verified downloads, 128 MiB hard cap
```

Do not refactor beta-named transport modules during unrelated queue/reliability work.

---

## 5. Current queue/worker policy

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
manual_stale_match_retry_policy = single_attempt_then_defer
automatic_first_attempt_credit_policy = fresh_at_activation_until_first_attempt
deferred_clear_policy = explicit_button_deferred_android_only
```

Important behavior:

- fresh motion may preempt an older event during a retry wait;
- an active camera/download attempt is never cancelled by newer motion;
- stale backlog persists but does not automatically wake the camera;
- explicit manual recovery may process stale backlog;
- old manual `RECORDING_MATCH_ERROR` gets one attempt then immediate defer;
- a fresh automatic event accepted while the worker is busy keeps one runtime first-attempt credit until its first real attempt begins;
- the first-attempt credit is runtime-only and is not restored after HA restart;
- duplicate notification events may complete through persistent recording fingerprint dedupe before cmd13/cmd8;
- explicit deferred cleanup removes only deferred Android pending entries and preserves processed-event and completed-recording history.

---

## 6. Reliability history retained

```text
v1.3.8  partial connection_closed -> 3/6-second fast retry
v1.3.9  partial idle_timeout -> 3/6-second fast retry
v1.3.10 recording priority over Live View
v1.3.11 preserve Live View HTTP/go2rtc consumers during recording priority
v1.3.12 queued/deferred visibility + explicit all-pending manual recovery
v1.3.13 stale manual RECORDING_MATCH_ERROR -> one attempt then defer
v1.3.14 fresh-at-activation automatic first-attempt credit
v1.3.15 explicit queue-only cleanup of dead deferred Android backlog
```

---

## 7. v1.3.14 field validation — automatic event success

After installing v1.3.14 and restarting Home Assistant, a new Reolink Android motion arrived:

```text
notification UTC:   2026-08-28T14:00:28.957000+00:00
Israel local time:  17:00:28
camera:              atv
last_event_matched:  true
last_camera_mapped:  true
last_event_queued:   true
duplicate_rejected:  false
processing lag:      0.248 s
```

The automatic worker gave the event first-attempt credit:

```text
automatic_first_attempt_credits_granted: 1
automatic_first_attempt_credit_pending:  0
automatic_late_first_attempts:           0
```

The first attempt started at approximately 17:01:28 local and hit a physically known partial-stream failure:

```text
failure_stage: STREAM_REMOTE_DISCONNECT_INCOMPLETE
failure_type: remote_disconnect_before_expected_size
written: 2,981,888
expected: 9,421,184
termination: connection_closed
uid_resolve_succeeded: true
```

The existing v1.3.8 short recovery path retried. The second attempt started at approximately 17:02:24 local and completed at approximately 17:04:48 local:

```text
file_bytes_written:       9,421,184
xml_reported_size:        9,421,184
final_size_match:         true
mp4_ftyp_valid:           true
fsync_completed:          true
atomic_rename_completed:  true
file_saved:               true
last_ready_event_fired:   true
termination_reason:       expected_size_reached
```

Conclusion: v1.3.14 preserved the proven automatic end-to-end path and the partial-stream fast recovery path.

---

## 8. 2026-08-28 manual recovery field run — first pass

Immediately before pressing `Download missing recordings / הורד סרטונים חסרים`:

```text
pending_count:              15
completed_recording_count:  35
worker.attempts:            2
worker.retries:             1
worker.completed:           1
persistent_deferred_count:  15
manual_recovery_requests:   0
```

After one explicit recovery pass:

```text
pending_count:                  5
completed_recording_count:      45
worker.attempts:                19
worker.retries:                 3
worker.completed:               11
persistent_deferred_count:      5
manual_recovery_requests:       1
manual_recovery_last_queued:    15
manual_recovery_rearmed:        15
manual_recovery_remaining:      0
manual_stale_match_single_attempts: 4
```

Arithmetic:

```text
17 new attempts = 15 first attempts + 2 retries
10 new completed recordings = 45 - 35
```

Therefore all 15 queued events received work:

- 10 events successfully resolved to recordings and completed;
- 4 stale historical events hit `RECORDING_MATCH_ERROR` and correctly took one attempt each;
- 1 remaining event consumed the two additional retries, for three attempts total, and still failed/deferred.

This confirms v1.3.13's failure-aware stale manual retry optimization remains correct while transient failures retain retries.

---

## 9. Second manual recovery pass — proof of dead backlog

A second explicit recovery pass was performed against the remaining five deferred events.

Before second pass:

```text
pending_count:       5
worker.attempts:     19
worker.retries:      3
worker.completed:    11
manual_stale_match_single_attempts: 4
```

After second pass:

```text
pending_count:       5
completed_recording_count: 45
worker.attempts:     26
worker.retries:      5
worker.completed:    11
persistent_deferred_count: 5
manual_recovery_requests: 2
manual_recovery_last_queued: 5
manual_recovery_rearmed: 20
manual_recovery_remaining: 0
manual_stale_match_single_attempts: 8
```

Arithmetic:

```text
7 new attempts = 4 single attempts + 1 event x 3 attempts
2 new retries
0 new completed recordings
pending stayed 5 -> 5
```

Conclusion: the five remaining queue entries were demonstrated in two recovery passes to be non-recoverable with the current SD/FileInfo state. Repeated recovery would only wake the battery camera again and spend retries without reducing the queue.

At the end of the second pass:

```text
worker.running: false
worker.pending_trigger: false
worker.waiting_camera_closed: false
manual_recovery_remaining: 0
```

No worker operation was stuck.

---

## 10. v1.3.15 decision — explicit cleanup of dead deferred backlog

The visible `Recordings queued / סרטונים בתור` sensor counts pending Android notification events. A deferred event remains pending by design, so the five proven-dead historical events would otherwise leave the visible queue permanently at five.

v1.3.15 adds an explicit Home Assistant button:

```text
Clear deferred recordings / נקה סרטונים שנדחו
```

The button is intentionally destructive only to queue metadata, not to camera media or verified files.

Behavior:

```text
user presses Clear deferred recordings
-> select pending events where source == android_notification
   AND event_id is currently deferred
-> remove those entries from pending queue
-> remove their deferred metadata
-> save queue once
-> clean matching runtime worker ID sets
-> refresh queue counts
```

Explicit exclusions:

- does not contact or wake the camera;
- does not query UID, auth, FileInfo, cmd13 or cmd8;
- does not delete any MP4 file;
- does not remove completed-recording fingerprints;
- does not remove processed-event IDs;
- does not remove a fresh/non-deferred pending event;
- does not clear non-Android deferred events through this button.

Keeping processed-event IDs is intentional: if Android/Companion later surfaces the same historical notification again, the cleared event remains deduplicated and cannot silently repopulate the queue.

---

## 11. v1.3.15 implementation

### Event queue

`custom_components/reolink_battery/events.py`

Adds:

```text
EventQueue.discard_deferred(event_ids)
```

It removes only requested event IDs that are both pending and deferred. It leaves `_processed` and `_completed_recordings` unchanged.

### Coordinator

`custom_components/reolink_battery/coordinator.py`

Adds:

```text
async_clear_deferred_events(source=None)
```

The Home Assistant button calls it with:

```text
source = android_notification
```

The coordinator saves queue storage once after the bulk removal.

### Worker

`custom_components/reolink_battery/recording_worker_v138.py`

Adds:

```text
async_clear_deferred_recordings()
```

After coordinator removal it discards removed IDs from:

```text
_activated_event_ids
_manual_recovery_event_ids
_automatic_first_attempt_credit_event_ids
```

and refreshes pending/deferred counters.

### Home Assistant button

`custom_components/reolink_battery/button.py`

Adds:

```text
ReolinkClearDeferredRecordingsButton
translation_key = clear_deferred_recordings
icon = mdi:playlist-remove
```

The button is available only when at least one pending Android event is currently deferred.

---

## 12. New v1.3.15 diagnostics

Secret-safe runtime counters:

```text
deferred_clear_requests
deferred_clear_last_count
deferred_cleared_total
deferred_clear_policy = explicit_button_deferred_android_only
```

For the current physical field state, after upgrading to v1.3.15 and pressing the new cleanup button once, the expected result is approximately:

```text
deferred_clear_requests:   1
deferred_clear_last_count: 5
deferred_cleared_total:    5
pending_count:              0
persistent_deferred_count:  0
```

provided no new notification arrives between upgrade and button press.

---

## 13. Regression coverage

New test:

```text
tests/test_clear_deferred_recordings_v1315.py
```

It verifies:

1. only selected deferred pending events are removed;
2. non-deferred pending events remain untouched;
3. non-selected deferred events remain untouched;
4. processed-event count/history is retained;
5. after storage round-trip a cleared event does not return;
6. re-enqueueing the same cleared historical event ID is rejected by processed-event dedupe;
7. coordinator cleanup is persisted;
8. worker cleanup is Android-only and removes matching runtime IDs;
9. the Home Assistant button calls only the queue cleanup worker API;
10. English/Hebrew entity names exist;
11. the cleanup layer does not reimplement cmd13/cmd8/ACK transport.

CI runs this regression alongside all existing suites.

---

## 14. Files changed for v1.3.15

```text
custom_components/reolink_battery/events.py
custom_components/reolink_battery/coordinator.py
custom_components/reolink_battery/recording_worker_v138.py
custom_components/reolink_battery/button.py
custom_components/reolink_battery/strings.json
custom_components/reolink_battery/translations/en.json
custom_components/reolink_battery/translations/he.json
tests/test_clear_deferred_recordings_v1315.py
.github/workflows/validate.yaml
custom_components/reolink_battery/manifest.json
CHANGELOG.md
CHECKPOINT.md
docs/checkpoints/v1.3.15.md
```

No recording transport module is intentionally changed.

---

## 15. What v1.3.15 deliberately does NOT change

Unchanged:

- Android notification matching/fingerprint logic;
- 60-second recording settle;
- 600-second definition of normal automatic freshness;
- v1.3.14 fresh-at-activation first-attempt credit;
- startup stale/deferred policy;
- explicit manual recovery semantics;
- v1.3.13 stale manual `RECORDING_MATCH_ERROR` single-attempt optimization;
- automatic max-three-attempt bound once work is selected;
- normal 30/60-second retry delays;
- partial-stream 3/6-second recovery;
- active-attempt non-cancellation;
- retry-wait newest-motion preemption;
- FileInfo query/candidate logic;
- cmd13 wire format;
- cmd8 wire format;
- full-high/mainStream selection;
- heartbeat behavior;
- UDP ACK behavior;
- authoritative collector sizing;
- exact-size acceptance;
- partial cleanup;
- persistent recording fingerprint dedupe;
- Live View consumer preservation;
- single-local-session invariant.

---

## 16. Next physical validation after installing v1.3.15

1. Update through HACS and restart Home Assistant.
2. Confirm diagnostics version is `1.3.15`.
3. Before pressing cleanup, confirm the current old field state is still approximately:

```text
Recordings queued:   5
Recordings deferred: 5
Recovery remaining:  0
```

4. Press **`נקה סרטונים שנדחו`** once.
5. Do not expect camera activity; this action must be queue-only.
6. Confirm:

```text
Recordings queued:   0
Recordings deferred: 0
```

unless a genuinely new Android notification arrived in the meantime.
7. Export diagnostics and verify:

```text
deferred_clear_requests >= 1
deferred_clear_last_count = 5   # expected for the current field state
deferred_cleared_total >= 5
```

8. Confirm `worker.attempts` and `worker.retries` did not increase because of the cleanup button.
9. Create one fresh motion after cleanup and confirm the normal automatic download/Telegram path still works.

---

## 17. New-chat kickoff

```text
Continue development of Dmxsir/ha-reolink-battery.

Read CHECKPOINT.md completely first and treat it as authoritative.
Current release target is v1.3.15 on Reolink Argus 2E.

The recording transport is physically proven. Do not rewrite cmd13/cmd8,
heartbeat, UDP ACK or exact-size MP4 verification without new physical evidence.

v1.3.14 was physically validated on 2026-08-28: a fresh automatic motion at
17:00:28 Israel downloaded successfully after the first transfer ended partial
at 2,981,888 / 9,421,184 and the existing fast retry completed the exact MP4.

Manual recovery then reduced backlog from 15 to 5 and completed 10 recordings.
A second recovery pass against those five produced 7 more attempts, 2 retries,
0 completions and left pending/deferred at 5. Four were repeated stale
RECORDING_MATCH_ERROR single-attempt misses and the fifth exhausted 3 attempts.

v1.3.15 adds an explicit queue-only button `Clear deferred recordings` /
`נקה סרטונים שנדחו`. It removes only pending Android events currently marked
deferred, preserves processed-event dedupe and completed-recording fingerprints,
does not delete MP4 files and never contacts/wakes the camera.

Next physical validation: install v1.3.15, press the new cleanup button once,
expect queued/deferred 5 -> 0 and no increase in recording worker attempts.
```

---

## 18. Handoff summary

```text
Android notification ingestion                     working
persistent queue / stale backlog policy            working
recording fingerprint dedupe                       physically exercised
UID discovery / legacy auth                        physically proven
FileInfo lookup                                    physically proven
cmd13/cmd8 request shape                           physically proven
full exact MP4 download                            physically proven
recording-ready / Telegram end-to-end              physically proven
partial remote-close fast recovery                 v1.3.8, physically revalidated 2026-08-28
partial idle-timeout fast recovery                 v1.3.9
recording priority over Live View                  v1.3.10
Live View consumer preservation                    v1.3.11
queued recordings + manual recovery                v1.3.12
stale manual match-miss optimization               v1.3.13 physically validated
fresh automatic starvation protection              v1.3.14 physically validated normal path
explicit dead deferred backlog cleanup             v1.3.15 pending field validation
```
