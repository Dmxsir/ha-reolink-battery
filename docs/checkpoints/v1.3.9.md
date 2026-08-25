# Reolink Battery Development Checkpoint

**Checkpoint version:** v1.3.9  
**Checkpoint date:** 2026-08-25  
**Repository:** `Dmxsir/ha-reolink-battery`  
**Current stable release:** `v1.3.9`  
**Current stable main commit:** `906b9f95ba8d8f81473fe4924d76dcd93efd6cd3`  
**Primary field-test camera:** Reolink Argus 2E  
**Home Assistant field environment:** HA 2026.8.2 / HA OS 18.2 / Python 3.14.6 / x86_64  
**Camera firmware observed:** `0616_722_52_478`  
**Camera hardware observed:** `BIPC_36S7616MC94`

---

## 1. Purpose of this checkpoint

This file is the authoritative handoff document for continuing development in a new ChatGPT/Codex session without reconstructing the project history from conversation logs.

Read this file completely before changing recording transport behavior.

The integration is already capable of completing the full intended workflow on real hardware. The remaining work is reliability hardening around intermittent Argus battery-camera transfer stalls, not discovery of the basic download protocol.

Every future stable version bump must update this root `CHECKPOINT.md` and create a matching immutable archive at:

```text
docs/checkpoints/v<version>.md
```

CI/release validation is intended to enforce this policy.

---

## 2. Product goal

Build a battery-safe Home Assistant integration for Reolink battery cameras, primarily validated against the **Argus 2E**.

The desired automatic path is:

```text
Reolink motion
  -> official Reolink Android notification
  -> Home Assistant Companion Last Notification sensor
  -> Reolink Battery notification bridge
  -> persistent event queue
  -> camera remains closed during settle period
  -> short local Baichuan/P2P wake + login
  -> FileInfo recording lookup
  -> cmd13 prepare
  -> cmd8 full recording download
  -> exact MP4 validation + atomic save
  -> reolink_battery_recording_ready HA event
  -> optional Telegram automation
```

Battery safety is a primary design constraint. The integration must not turn the Argus into a permanently connected camera.

No continuous RTSP/ONVIF/polling session is required for the automatic recording path.

---

## 3. Authentication and device behavior already established

The tested Argus 2E uses the legacy local credential path:

```text
auth_path = legacy_local_credential
```

Cloud account authentication by itself is not sufficient for local Baichuan operations. The camera requires its local device administrator credentials.

Previously established device/auth capability conclusions:

- cloud account session works;
- camera is bound to the account;
- local device password is required;
- no protocol-3/sigV3 path is available on this unit;
- local legacy login is the working path;
- UID discovery/wake over LAN/P2P is functional;
- successful authentication has repeatedly been observed on the physical camera.

Do **not** interpret every later `AUTH_ERROR` as a bad password. On a battery camera a transient wake/readiness race can also produce a login-stage failure after UID resolution succeeds.

---

## 4. Physically validated recording protocol

The core recording download protocol has already been proven with both the official Windows SDK oracle and the portable integration implementation.

### Official SDK oracle result

On the Argus 2E the official SDK successfully performed:

```text
LAN connection -> OPEN_SUCCESS
recording FileInfo query -> response 0
cmd16 observed before download
cmd13 -> accepted
cmd8 -> accepted
full recording bytes received
```

One oracle recording was downloaded completely at the exact reported size:

```text
10,521,742 / 10,521,742 bytes
```

### Validated portable transport shape

The integration's stable recording path retains the beta-era modules because that path was built incrementally and physically validated.

Important validated behavior includes:

- cmd13 prepare request;
- returned download handle;
- cmd8 request using full-high `FileInfo` layout;
- forced `mainStream` behavior on the tested unit;
- P2P heartbeat every ~1 second;
- fresh heartbeat transaction IDs activated only after login;
- periodic UDP ACK behavior at ~10 ms cadence;
- exact-size MP4 verification;
- atomic `.part` -> `.mp4` finalization;
- failed/incomplete `.part` removal;
- recording-ready event only after a verified final file exists.

The stable transport baseline ultimately derives from the retained diagnostic beta chain, especially the behavior established around `v0.1.2-beta.40` through `v0.1.2-beta.45`.

**Do not casually rewrite cmd13/cmd8, heartbeat, ACK bitmap behavior, framing, payload offsets, or file collector logic while investigating worker retry timing.** Those pieces have already produced successful full downloads and successful Telegram end-to-end delivery.

---

## 5. Current automatic worker policy

Base worker constants currently retained:

```text
RECORDING_SETTLE_SECONDS = 60.0
MAX_ATTEMPTS_PER_TRIGGER = 3
RETRY_DELAYS_SECONDS = (30.0, 60.0)
```

The v1.3.8/v1.3.9 recovery layer adds:

```text
INCOMPLETE_STREAM_RETRY_DELAYS_SECONDS = (3.0, 6.0)
```

### Queue / backlog rules

Current policy is intentionally battery-safe:

- automatic events have a freshness window of 600 seconds;
- newest explicitly activated fresh Android event wins;
- stale backlog does not wake the camera;
- events that exhaust retries are persistently deferred;
- deferred events are not automatically rearmed;
- startup recovery selects at most the newest fresh non-deferred event;
- a newer notification can preempt an older event **during a retry wait**;
- an active camera/download session is not cancelled merely because a new motion notification arrives.

Important diagnostics policy names currently expected:

```text
selection_policy = newest_activated_fresh_first
deferred_rearm_policy = explicit_only
startup_recovery_policy = newest_fresh_non_deferred_only
retry_preemption_policy = newer_notification_before_retry
```

---

## 6. v1.3.7 field failure that led to v1.3.8

A real motion notification was accepted and queued correctly.

The automatic flow progressed all the way into the recording download:

```text
notification bridge       OK
queue                      OK
UID resolve                OK
authentication             OK
FileInfo lookup            OK
candidate selection        OK
cmd13                      OK
cmd8                       OK
MP4 bytes                  started
exact final file           FAILED
```

Key stream telemetry from the real Argus run:

```text
xml_reported_size:          9,555,011
file_bytes_written:         3,342,336
mp4_ftyp_valid:             true
remote_disconnect_observed: true
termination_reason:         connection_closed
elapsed_seconds:            46.93
```

Approximately 35% of the MP4 arrived before the camera closed the P2P session.

The incomplete `.part` file was correctly rejected and removed:

```text
part_created:               true
final_size_match:           false
atomic_rename_completed:    false
file_saved:                 false
part_removed_on_failure:    true
```

The transport request side itself looked healthy:

```text
cmd13_udp_ack_received:     true
cmd13_udp_retransmit_count: 0
cmd8_udp_ack_received:      true
cmd8_udp_retransmit_count:  0
```

The stream also showed reordering/gaps, but the important field at disconnect was the current active missing window, not the historical cumulative unresolved count.

At disconnect the active missing packet count was only a small number of packets; therefore v1.3.8 did **not** rewrite the proven ACK algorithm. Instead it addressed retry timing.

### v1.3.8 change

A partial transfer is classified as:

```text
STREAM_REMOTE_DISCONNECT_INCOMPLETE
```

only when all of the following are true:

```text
remote_disconnect_observed == true
termination_reason == connection_closed
file_bytes_written > 0
expected_size > file_bytes_written
```

Once that narrow condition is observed, the remaining bounded retries use:

```text
3 seconds
6 seconds
```

instead of 30/60 seconds, attempting to reuse the battery-camera awake window.

PR: `#10` — `v1.3.8: Recover incomplete Argus recording streams`  
Release: `v1.3.8`

---

## 7. v1.3.8 field failure that led to v1.3.9

After installing v1.3.8, a new real-camera failure signature appeared.

The prior attempt telemetry reported:

```text
event_time:                 2026-08-25T14:18:28.832000+00:00
attempt_time:               2026-08-25T14:19:28.832908+00:00
failure_stage:              RECORDING_FILE_NOT_VERIFIED
uid_resolve_succeeded:      true
uid_resolve_elapsed_ms:     839.882
stream_termination_reason:  idle_timeout
stream_elapsed_seconds:     51.328
stream_file_bytes:          1,261,568
stream_expected_size:       9,314,178
stream_remote_disconnect:   false
```

This was clearly another **real partial MP4 transfer**, but unlike the v1.3.7 field failure the camera did not explicitly close the P2P connection. Data simply stopped arriving until the stream idle timeout fired.

Because v1.3.8 deliberately recognized only the explicit `connection_closed` signature, it did not classify this as fast recovery.

### Newer-motion preemption observed in the same field test

A second motion notification arrived at:

```text
2026-08-25T14:20:28.885000+00:00
```

The worker correctly preempted the older event's retry wait:

```text
retry_preemptions:          1
last_preempted_event_time:  2026-08-25T14:18:28.832000+00:00
last_preempting_event_time: 2026-08-25T14:20:28.885000+00:00
```

This behavior is correct and must remain.

The diagnostics snapshot was taken while the worker was still running the newer event, which is why the live `stream_probe` had been reset to zero values at that exact moment. The useful previous-transfer data was preserved under `prior_attempt`.

---

## 8. v1.3.9 implementation

v1.3.9 extends fast recovery to the second observed partial-transfer failure mode.

### Explicit remote close

```text
partial MP4
+ remote_disconnect_observed
+ connection_closed
-> STREAM_REMOTE_DISCONNECT_INCOMPLETE
-> retry 3s / 6s
```

### Partial idle stall

```text
partial MP4
+ idle_timeout
-> STREAM_IDLE_TIMEOUT_INCOMPLETE
-> retry 3s / 6s
```

For the idle-timeout path, actual transfer progress is mandatory:

```text
file_bytes_written > 0
expected_size > file_bytes_written
termination_reason == idle_timeout
```

Therefore the following still use the ordinary 30/60-second policy:

- zero-byte timeout;
- auth failure;
- FileInfo failure;
- routing/request failure;
- local filesystem failure;
- unrelated hard timeout;
- any failure with no authoritative larger expected size;
- any non-partial file condition.

### Files changed for v1.3.9

Primary behavior:

```text
custom_components/reolink_battery/recording_worker_v138.py
```

Regression coverage:

```text
tests/test_recording_stream_recovery_v138.py
```

Version/changelog:

```text
custom_components/reolink_battery/manifest.json
CHANGELOG.md
```

PR: `#11` — `v1.3.9: Recover partial streams after idle timeout`  
Merge commit: `906b9f95ba8d8f81473fe4924d76dcd93efd6cd3`  
Release: `v1.3.9`

The regression test includes the exact observed field signature:

```text
1,261,568 / 9,314,178 bytes
termination_reason = idle_timeout
```

All normal syntax/regression/integration tests and HACS validation passed before merge.

---

## 9. Most recent field environment / state before this checkpoint

Installed at the beginning of the latest field capture:

```text
reolink_battery = 1.3.8
Home Assistant = 2026.8.2
HA OS = 18.2
Python = 3.14.6
```

v1.3.9 was subsequently built, tested, merged and released. The next physical test must therefore start by confirming the installed diagnostics show:

```text
"version": "1.3.9"
```

The notification bridge itself is currently considered healthy. Recent field diagnostics showed:

```text
configured: true
listener_active: true
last_event_matched: true
last_camera_mapped: true
last_event_queued: true
duplicate_rejected: false
```

The primary reliability problem is still the media transfer completing consistently on the sleeping/battery-powered Argus, not Android notification ingestion.

---

## 10. Exact next field test

After installing v1.3.9 and restarting Home Assistant:

1. Confirm diagnostics report integration version `1.3.9`.
2. Trigger one real motion event in front of camera `atv`.
3. Avoid intentionally triggering another motion during the first test if possible; this makes retry timing easier to interpret.
4. Allow the worker to finish or exhaust its bounded attempts.
5. Export integration diagnostics.
6. Inspect the fields below before changing code.

### Success condition

Expected successful final telemetry:

```text
file_saved: true
final_size_match: true
atomic_rename_completed: true
last_file_saved: true
last_ready_event_fired: true
recording worker completed increases
completed_recording_count increases
```

The Home Assistant event should fire:

```text
reolink_battery_recording_ready
```

If the user's Telegram automation is enabled, the recording should then be sent by that automation.

### If the first transfer ends with explicit remote close

Expect:

```text
last_failure_stage = STREAM_REMOTE_DISCONNECT_INCOMPLETE
```

and, unless a newer motion preempts the wait, another attempt should begin after approximately **3 seconds**.

### If the first transfer stalls with idle timeout after bytes were written

Expect:

```text
last_failure_stage = STREAM_IDLE_TIMEOUT_INCOMPLETE
```

and, unless a newer motion preempts the wait, another attempt should begin after approximately **3 seconds**.

### If a newer event arrives

It is valid for the worker to show:

```text
retry_preemptions > 0
```

and move to the newer event instead of retrying the old one.

Do not call that a retry failure; it is deliberate queue policy.

---

## 11. Diagnostics fields that matter most next

### Worker

```text
recording_worker.running
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

### Stream

```text
download_prepare.stream_probe.file_bytes_written
download_prepare.stream_probe.xml_reported_size
download_prepare.stream_probe.final_size_match
download_prepare.stream_probe.file_saved
download_prepare.stream_probe.termination_reason
download_prepare.stream_probe.elapsed_seconds
download_prepare.stream_probe.remote_disconnect_observed
download_prepare.stream_probe.expected_size_reached
download_prepare.stream_probe.expected_size_match
download_prepare.stream_probe.cmd13_udp_ack_received
download_prepare.stream_probe.cmd8_udp_ack_received
download_prepare.stream_probe.udp_current_missing_packet_count_at_disconnect
download_prepare.stream_probe.udp_max_gap_recovery_ms
```

### Notification / queue

```text
notification_bridge.last_event_matched
notification_bridge.last_camera_mapped
notification_bridge.last_event_queued
notification_bridge.duplicate_rejected
recording_worker.eligible_fresh_pending_count
recording_worker.stale_pending_count
recording_worker.deferred_count
```

---

## 12. Important interpretation rules

### A. `udp_unresolved_missing_packet_count_at_disconnect`

Do not automatically treat this cumulative/historical count as the number of packets missing at the final active receive window.

Prefer the current-at-disconnect fields, especially:

```text
udp_current_missing_packet_count_at_disconnect
udp_expected_next_seq_at_disconnect
udp_highest_network_seq_seen
udp_buffered_out_of_order_at_disconnect
```

### B. Zeroed `stream_probe` while worker is running

The current stream telemetry may be reset for a newer attempt. If diagnostics are captured mid-run, inspect `recording_worker.prior_attempt` to recover the immediately preceding failure.

### C. `AUTH_ERROR` after successful UID resolution

Do not immediately conclude the local camera password is wrong. Field history has shown intermittent battery-camera wake/readiness behavior. Compare with prior attempts and whether the same credentials recently progressed through FileInfo/cmd13/cmd8.

### D. `RECORDING_FILE_NOT_VERIFIED`

This is the base generic worker result. In v1.3.9, a qualifying partial `connection_closed` or partial `idle_timeout` should then be promoted by the recovery worker to a specific `STREAM_*_INCOMPLETE` stage.

---

## 13. What not to change yet

Until a clean v1.3.9 field run is collected, avoid changing multiple independent variables simultaneously.

In particular, do **not** simultaneously change:

- 60-second initial settle time;
- cmd13 body/framing;
- cmd8 full-high/mainStream layout;
- heartbeat interval/transaction-ID policy;
- UDP ACK cadence/bitmap semantics;
- exact-size MP4 verification;
- three-attempt bound;
- newest-motion preemption policy.

The current experiment is specifically testing whether **retry timing after a proven partial transfer** is the missing reliability piece.

Changing transport and wake timing at the same time would make the next physical result ambiguous.

---

## 14. Candidate next investigations only if v1.3.9 still fails

These are hypotheses, not approved changes yet.

### A. Repeated partial transfer even with 3/6-second retries

If multiple consecutive attempts begin successfully but repeatedly stop mid-file, investigate UDP gap recovery and/or camera-side pacing using the detailed transport telemetry.

Before changing ACK behavior, compare successful historical transport traces against failed traces.

### B. First attempt succeeds in FileInfo but retries fail auth/wake immediately

Consider whether reconnect strategy after an incomplete stream needs a specialized short wake/auth recovery path.

### C. 60-second settle may be too long

This is a later experiment only.

Reason: Android push occurs close to motion/recording start and many observed clips are ~29 seconds. A 60-second settle guarantees the clip should be finalized, but it may also allow the battery camera to return to deeper sleep before the first download session.

If v1.3.9 does not solve reliability, test settle time independently (for example 40-45 seconds) rather than changing it together with UDP transport.

### D. Better diagnostics

Potential future telemetry:

```text
fast_recovery_active
fast_retry_index
selected_retry_delay_seconds
last_progress_age_ms
current_gap_age_ms
camera_ready_elapsed_ms
auth_attempt_count
file_info_open_attempt_count
```

These can be added without changing wire behavior.

---

## 15. Important source files

### Automatic worker / policy

```text
custom_components/reolink_battery/recording_worker.py
custom_components/reolink_battery/recording_worker_v138.py
```

### Recording lookup / download chain

```text
custom_components/reolink_battery/recording_probe.py
custom_components/reolink_battery/recording_download_probe.py
custom_components/reolink_battery/recording_download_probe_beta20.py
custom_components/reolink_battery/recording_download_probe_beta21.py
custom_components/reolink_battery/recording_download_beta22.py
```

Retained beta-named modules are intentional. Do not delete/refactor them as part of a reliability fix unless behavior preservation is separately demonstrated.

### Integration wiring / diagnostics

```text
custom_components/reolink_battery/__init__.py
custom_components/reolink_battery/diagnostics.py
custom_components/reolink_battery/notification_bridge.py
custom_components/reolink_battery/coordinator.py
```

### Tests

```text
tests/test_notification_bridge.py
tests/test_recording_backlog_policy.py
tests/test_recording_stream_recovery_v138.py
tests/test_integration_load.py
```

---

## 16. Release/checkpoint policy from this version onward

For **every new integration version**:

1. bump `custom_components/reolink_battery/manifest.json`;
2. update `CHANGELOG.md`;
3. update root `CHECKPOINT.md` so it describes the new exact state;
4. create an archive copy at `docs/checkpoints/v<version>.md`;
5. include the new field evidence / decisions / next validation target;
6. include the new stable commit/PR/release identifiers once known;
7. ensure CI checkpoint validation passes;
8. do not publish a GitHub release if the checkpoint does not match the manifest version.

The checkpoint is part of the release artifact/documentation, not an optional afterthought.

---

## 17. New-chat kickoff prompt

Copy/paste this into a new chat when continuing the project:

```text
המשך את פרויקט Dmxsir/ha-reolink-battery.

קודם קרא במלואו את CHECKPOINT.md בריפו ואת ה-checkpoint הארכיוני של הגרסה הנוכחית תחת docs/checkpoints/.
אל תשחזר הנחות ישנות לפני שקראת אותם.

אנחנו כרגע אחרי release v1.3.9.
המסלול cmd13/cmd8 וה-transport הבסיסי כבר הוכחו פיזית, כולל הורדות מלאות בעבר.
המיקוד הנוכחי הוא אמינות הורדת ההקלטה מ-Argus 2E, ובעיקר fast recovery לאחר partial stream מסוג connection_closed או idle_timeout.

קודם בדוק את הגרסה/commit הנוכחיים בריפו ואת ה-checkpoint, ואז נתח את הדיאגנוסטיקה/תוצאת field test שאצרף.
אל תשנה את cmd13/cmd8/heartbeat/UDP ACK/settle time יחד ללא ראיה חדשה שמצדיקה זאת.

בכל גרסה חדשה שתיווצר, עדכן גם CHECKPOINT.md וצור docs/checkpoints/v<version>.md מלא לפני release.
```

---

## 18. Current handoff state

At the moment this checkpoint was written:

- v1.3.9 is the latest stable release;
- the v1.3.9 idle-timeout recovery patch has passed automated regression and HACS validation;
- the next required evidence is a **physical v1.3.9 motion event** from the Argus 2E;
- no further transport modification should be made before examining that result unless an independent reproducible bug is discovered.

**Next action:** install/confirm v1.3.9 in Home Assistant, trigger one clean real motion event, then analyze the completed diagnostics using sections 10-12 above.
