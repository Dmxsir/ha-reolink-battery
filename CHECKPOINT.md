# Reolink Battery Development Checkpoint

**Checkpoint version:** v1.3.14  
**Checkpoint date:** 2026-08-28  
**Repository:** `Dmxsir/ha-reolink-battery`  
**Release target:** `v1.3.14`  
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

## 5. Queue/worker baseline before v1.3.14

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

Important behavior retained:

- fresh motion may preempt an older event during a retry wait;
- an active camera/download attempt is never cancelled by newer motion;
- stale backlog persists but does not automatically wake the camera;
- explicit manual recovery may process stale backlog;
- duplicate notification events may complete through persistent recording fingerprint dedupe before cmd13/cmd8.

---

## 6. Reliability history retained

```text
v1.3.8  partial connection_closed -> 3/6-second fast retry
v1.3.9  partial idle_timeout -> 3/6-second fast retry
v1.3.10 recording priority over Live View
v1.3.11 preserve Live View HTTP/go2rtc consumers during recording priority
v1.3.12 queued/deferred visibility + explicit all-pending manual recovery
v1.3.13 stale manual RECORDING_MATCH_ERROR -> one attempt then defer
```

v1.3.13 field validation on 2026-08-28 confirmed:

```text
manual_stale_match_single_attempts: 8
manual_stale_match_retry_policy: single_attempt_then_defer
last_deferred_reason: manual_stale_recording_match_miss
```

This validates the v1.3.13 optimization. Transient UID/auth/wake failures still retain normal retries.

---

## 7. 2026-08-28 field evidence leading to v1.3.14

Diagnostics showed a new Reolink Android notification:

```text
last_reolink_notification_time: 2026-08-28T12:44:42.038000+00:00
local Israel time: 15:44:42
last_reolink_notification_camera: atv
last_event_matched: true
last_camera_mapped: true
last_event_queued: true
duplicate_rejected: false
last_processing_lag_seconds: 0.196
```

The user physically confirmed that the only MP4 downloaded and sent to Telegram during the later run was an older **13:52** recording, and that it was delivered at approximately **16:12**.

Diagnostics independently showed:

```text
recording_worker.last_completed_time:
2026-08-28T13:12:11.816050+00:00
local Israel time: 16:12:11
```

The fresh 15:44 motion was therefore accepted and queued but was not the 16:12 successful recording.

At the diagnostic capture:

```text
pending_count: 15
deferred_count: 15
persistent_deferred_count: 15
eligible_fresh_pending_count: 0
stale_pending_count: 15
manual_recovery_remaining: 0
```

The latest visible failure telemetry had already been overwritten by later old-backlog work, so it did not directly identify the 15:44 event's final disposition.

---

## 8. Root-cause code analysis

The worker is serialized. If a new notification arrives while `_process_once()` is already executing for an older manual/backlog event, the new notification cannot cancel that active camera operation. That is intentional.

The problem was what happened after the old operation returned.

The v1.3.13 loop performed:

```text
finish current active attempt
-> _defer_stale_activated_events(now)
-> _next_android_event()
```

`_defer_stale_activated_events()` deferred activated automatic events older than 600 seconds before `_next_android_event()` could select them.

Therefore this field sequence was possible:

```text
15:44 fresh automatic event accepted and activated
-> worker is already busy with old/manual work
-> active work continues until about 16:12
-> fresh event is now about 27 minutes old
-> stale sweep runs before next selection
-> event can be deferred without a first attempt
```

This is a queue-admission starvation bug, not evidence of a cmd13/cmd8 transport regression.

---

## 9. v1.3.14 fix — fresh-at-activation first-attempt credit

Implemented only in:

```text
custom_components/reolink_battery/recording_worker_v138.py
```

New runtime policy:

```text
IF an Android automatic event reaches normal notify(event_id)
AND it is non-deferred
AND it is fresh at that moment
THEN grant one runtime first-attempt credit

WHILE credit is pending
- stale sweep must not defer that event
- newest-event selection may select it even after it ages past 600 seconds

IMMEDIATELY BEFORE its first real worker attempt
- consume the credit

AFTER the attempt begins
- existing bounded retry behavior remains unchanged
```

The credit is deliberately **runtime-only**.

It is not persisted across Home Assistant restart. This preserves the existing startup invariant that stale backlog does not wake the battery camera merely because HA restarted.

Manual recovery does not call the automatic `notify()` path for backlog IDs and therefore does not manufacture automatic first-attempt credit for old events.

Already-stale automatic events also receive no credit.

Deferred events receive no credit.

---

## 10. Selection behavior after v1.3.14

Eligible candidates now include:

```text
explicit manual recovery event
OR fresh-at-activation automatic event with unconsumed first-attempt credit
OR normal activated automatic event that is still fresh
```

Selection remains newest-first by event time.

This means a new motion that arrived during a long manual-recovery camera operation will be selected ahead of the older manual backlog once the active operation finishes, even if the new event aged past ten minutes while waiting.

An active camera/download attempt is still not cancelled.

Retry-wait preemption remains unchanged.

---

## 11. New diagnostics

Secret-safe policy telemetry added:

```text
automatic_first_attempt_credit_pending
automatic_first_attempt_credits_granted
automatic_late_first_attempts
last_automatic_late_first_attempt_event_time
automatic_first_attempt_credit_policy
```

Expected policy value:

```text
automatic_first_attempt_credit_policy =
fresh_at_activation_until_first_attempt
```

Interpretation:

- `credit_pending`: accepted automatic events still owed their first attempt;
- `credits_granted`: credits consumed because first attempts actually started;
- `automatic_late_first_attempts`: first attempts that began after the event was already older than the normal 600-second freshness window;
- `last_automatic_late_first_attempt_event_time`: event timestamp of the latest such case.

No raw path, UID, IP address, recording filename or secret is exposed.

---

## 12. Regression coverage

New test:

```text
tests/test_fresh_automatic_first_attempt_v1314.py
```

It verifies:

1. an event accepted fresh receives runtime credit;
2. after simulating more than 10 minutes of worker delay, stale sweep does not defer it before the first attempt;
3. `_next_android_event()` still selects it;
4. consuming the credit records a late first attempt;
5. once credit is consumed, normal stale deferral can apply again outside an active selected attempt;
6. an event already stale at `notify()` receives no credit;
7. an old manual event plus a fresh automatic event delayed by **27 minutes** selects the automatic event first.

CI workflow now runs this regression alongside the existing queue, stream-recovery, Live View, manual-recovery and integration-load suites.

---

## 13. Files changed for v1.3.14

```text
custom_components/reolink_battery/recording_worker_v138.py
tests/test_fresh_automatic_first_attempt_v1314.py
.github/workflows/validate.yaml
custom_components/reolink_battery/manifest.json
CHANGELOG.md
CHECKPOINT.md
docs/checkpoints/v1.3.14.md
```

No recording transport module is intentionally changed.

---

## 14. What v1.3.14 deliberately does NOT change

Unchanged:

- notification bridge matching/fingerprint logic;
- 60-second recording settle;
- 600-second definition of normal automatic freshness;
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
- persistent recording dedupe;
- Live View consumer preservation;
- single-local-session invariant.

---

## 15. Next physical validation

After installing v1.3.14:

1. Confirm diagnostics version is `1.3.14`.
2. Do **not** press recovery merely to validate normal motion.
3. Create a fresh motion event while the worker is idle and confirm normal download/Telegram behavior remains unchanged.
4. For the specific starvation test, manual recovery may be started only if there is safe backlog available, then create a fresh motion while the worker is inside an older active attempt.
5. If the fresh event passes 10 minutes before the worker becomes free, expected diagnostics are:

```text
automatic_first_attempt_credits_granted >= 1
automatic_late_first_attempts >= 1
last_automatic_late_first_attempt_event_time = <fresh motion timestamp>
```

6. The event must receive a real recording attempt instead of disappearing into `automatic_event_stale` before its first attempt.
7. Successful verified MP4 must still fire `reolink_battery_recording_ready` and continue to Telegram through the existing automation.
8. Old stale backlog after HA restart must remain dormant unless explicit manual recovery is pressed.

---

## 16. New-chat kickoff

```text
Continue development of Dmxsir/ha-reolink-battery.

Read CHECKPOINT.md completely first and treat it as authoritative.
Current release target is v1.3.14 on Reolink Argus 2E.

The recording transport is physically proven. Do not rewrite cmd13/cmd8,
heartbeat, UDP ACK or exact-size MP4 verification without new physical evidence.

v1.3.13 was physically validated: stale manual RECORDING_MATCH_ERROR events
receive one attempt then defer.

A 2026-08-28 field run then exposed automatic-event starvation: a motion at
15:44 was accepted/queued while old manual work remained active; the only MP4
delivered at 16:12 was the older 13:52 recording. Code analysis found that stale
automatic deferral ran before next-event selection, allowing an accepted fresh
event to age beyond 10 minutes and be deferred without a first attempt.

v1.3.14 adds runtime fresh-at-activation first-attempt credit. It never rearms
old backlog and is not persisted across restart. A credited event remains
eligible until its first worker attempt begins, after which existing bounded
retry behavior applies.

Next required step is physical validation of v1.3.14.
```

---

## 17. Handoff summary

```text
Android notification ingestion                     working
persistent queue / stale backlog policy            working
recording fingerprint dedupe                       physically exercised
UID discovery / legacy auth                        physically proven
FileInfo lookup                                    physically proven
cmd13/cmd8 request shape                           physically proven
full exact MP4 download                            physically proven
recording-ready / Telegram end-to-end              physically proven
partial remote-close fast recovery                 v1.3.8
partial idle-timeout fast recovery                 v1.3.9
recording priority over Live View                  v1.3.10
Live View consumer preservation                    v1.3.11
queued recordings + manual recovery                v1.3.12
stale manual match-miss optimization               v1.3.13 physically validated
fresh automatic starvation root cause              identified 2026-08-28
fresh-at-activation first-attempt credit            v1.3.14
v1.3.14 regression test                            added
v1.3.14 physical validation                        NEXT REQUIRED TEST
```

**Next action:** run CI for the v1.3.14 branch, then physically validate that a fresh automatic motion arriving during long manual work still receives its first recording attempt after the worker becomes available.
