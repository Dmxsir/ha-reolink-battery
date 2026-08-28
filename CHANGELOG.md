# Changelog

## v1.3.13 — Smart manual recovery and clearer queue state
- Uses field evidence from the v1.3.12 backlog test to stop wasting 30/60-second retries when an explicitly requested **stale** manual-recovery event reaches `RECORDING_MATCH_ERROR`: that historical event now gets one FileInfo matching attempt, is deferred with reason `manual_stale_recording_match_miss`, and the worker immediately advances to the next backlog item.
- Keeps normal bounded retries for fresh events and for transient failures such as `UID_RESOLVE_ERROR`, authentication/wake races, and other ordinary worker failures. A sleeping camera therefore still gets retry opportunities.
- Keeps the v1.3.8/v1.3.9 3/6-second fast-recovery policy for proven partial MP4 stream failures.
- Adds diagnostics `manual_stale_match_single_attempts` and `manual_stale_match_retry_policy=single_attempt_then_defer`.
- Adds separate Home Assistant sensors `Recordings deferred` / `סרטונים שנדחו` and `Recovery remaining` / `נותרו בשחזור` alongside the existing queued-recordings sensor.
- Extends `Recordings queued` attributes with `recovery_remaining`, `worker_running`, and `waiting_camera_closed` so the queue can be interpreted without exporting diagnostics.
- The new queue sensors only inspect in-memory coordinator/worker state and never contact the cloud or wake the battery camera.
- Leaves the automatic 60-second settle, 10-minute freshness rule, startup/deferred battery-safety policy, newest-motion preemption, completed-recording dedupe, Live View arbitration, cmd13/cmd8 framing, heartbeat, UDP ACK logic, and exact-size MP4 verification unchanged.

## v1.3.12 — Queue visibility and explicit backlog recovery
- Adds a Home Assistant sensor `Recordings queued` / `סרטונים בתור` that counts pending Android recording events without contacting or waking the camera.
- Adds queue-state attributes for `deferred`, `fresh_automatic`, and `stale` so the visible count can be interpreted without opening diagnostics.
- Adds a user-initiated `Download missing recordings` / `הורד סרטונים חסרים` button when the notification bridge/recording worker is configured.
- The button explicitly re-arms deferred Android events and marks all currently pending Android events as manual recovery work, including events older than the normal 10-minute automatic freshness window.
- Manual recovery remains serialized through the existing worker, uses the same recording-priority gate against Live View, keeps the three-attempt bound and existing fast partial-stream recovery, and still requires exact-size verified MP4 output before completing an event.
- Automatic startup behavior is unchanged: stale/deferred backlog still does not wake the battery camera merely because Home Assistant restarted. The freshness bypass exists only after an explicit user button press.
- Completed-recording fingerprint deduplication remains active during manual recovery, preventing already verified SD clips from being downloaded/sent again when multiple queued events map to the same physical recording.
- Adds secret-safe manual-recovery diagnostics (`manual_recovery_requests`, `manual_recovery_last_queued`, `manual_recovery_rearmed`, `manual_recovery_remaining`, `manual_recovery_policy`).
- Adds Hebrew and English entity translations plus dedicated regression coverage.
- Leaves cmd13/cmd8 framing, heartbeat, UDP ACK behavior, exact-size verification, recording settle timing and Live View consumer-preservation behavior unchanged.

## v1.3.11 — Preserve Live View consumers across recording priority
- Fixes a Live View regression reported after v1.3.10 where recording priority could stop the camera producer and `_finish_producer()` also published EOF (`None`) to every H264/AAC HTTP consumer, leaving the go2rtc-backed Live View dead instead of resuming reliably.
- Keeps v1.3.10 recording priority and the single-local-session invariant: recording still stops/yields the active camera-side Live View producer before taking `local_operation_lock`.
- During recording priority, preserves the existing HTTP/go2rtc consumer queues instead of closing them. New/reconnecting consumers may also queue while the recording owns priority.
- After the recording attempt, starts a fresh camera producer against those preserved consumers so Live View can resume without depending on an EOF-triggered go2rtc reconnect cycle.
- Retains normal EOF behavior for genuine Live View termination when recording priority is not active.
- Adds secret-safe `recording_preserved_finishes` and `recording_preserved_consumers` diagnostics and regression coverage verifying EOF is only emitted on the non-priority finish path.
- Leaves recording cmd13/cmd8 framing, heartbeat, UDP ACK behavior, exact-size verification, 60-second settle, bounded retries and v1.3.8/v1.3.9 incomplete-stream recovery unchanged.

## v1.3.10 — Recording priority over Live View
- Fixes a real field failure where a fresh recording event reached its 60-second settle point but then remained stuck for more than 17 minutes before UID discovery because an active Live View session held the shared `local_operation_lock`.
- Adds an explicit recording-priority gate to the on-demand Live View hub. When a recording attempt is ready, an active Live View producer is asked to stop and yield the shared camera-operation lease.
- Blocks new/reconnecting Live View producers while recording priority is active, preventing go2rtc from immediately reclaiming the lock before the recording worker can proceed.
- Restarts queued Live View consumers after the recording attempt finishes; a Live View restart problem is not allowed to turn a successfully verified recording into a worker failure.
- Adds secret-safe Live View diagnostics: `recording_priority_active`, `recording_priority_depth`, and `recording_preemptions`.
- Keeps one local Baichuan session at a time; it does not open recording and Live View connections concurrently.
- Leaves the validated recording transport unchanged: cmd13/cmd8 framing, heartbeat, periodic UDP ACK behavior, exact-size MP4 verification, partial-file cleanup, and the v1.3.8/v1.3.9 incomplete-stream 3/6-second recovery policy are unchanged.
- Adds regression coverage for the recording-vs-Live-View priority contract.

## v1.3.9 — Partial idle-timeout fast recovery
- Extends incomplete-stream recovery to the second real Argus failure signature: a valid MP4 transfer makes progress, remains smaller than the authoritative cmd13 size, then stalls until `idle_timeout` without an explicit remote disconnect.
- Classifies that case as `STREAM_IDLE_TIMEOUT_INCOMPLETE` and applies the same 3/6-second battery-awake retry window introduced in v1.3.8.
- Requires actual bytes written and an authoritative expected size larger than the partial file, so auth, FileInfo, routing, zero-byte and unrelated timeout failures retain the original 30/60-second policy.
- Leaves the validated cmd13/cmd8 framing, heartbeat, periodic UDP ACK behavior, exact-size verification, newest-motion preemption and partial-file cleanup unchanged.
- Adds deterministic regression coverage for the observed 1,261,568 / 9,314,178-byte `idle_timeout` signature.

## v1.3.8 — Incomplete stream fast recovery
- Detects the narrow failure where cmd13/cmd8 media transfer has started, MP4 bytes were written, the authoritative recording size is still larger than the collected file, and the Argus explicitly closes the P2P session.
- Classifies that case as `STREAM_REMOTE_DISCONNECT_INCOMPLETE` instead of the generic `RECORDING_FILE_NOT_VERIFIED` failure.
- Preserves the existing three-attempt bound but changes the remaining retry waits to 3 and 6 seconds after a confirmed partial remote disconnect, keeping retries inside the camera-awake window.
- Keeps 30/60-second retry timing for auth, FileInfo, routing, local file and other failures until a real incomplete stream has been observed.
- Leaves the validated cmd13/cmd8 framing, full-high/mainStream request, heartbeat cadence, periodic UDP ACK behavior, exact-size MP4 verification and partial-file cleanup unchanged.
- Adds deterministic regression coverage and CI validation for the recovery layer.

## v1.3.7 — Battery-safe motion backlog processing
- Requires explicit post-start state-change provenance before a stale Android notification can be promoted to an `android-repost`; a stale deterministic source never wakes the camera, while only its callback-time promoted event may wake.
- Persists automatic recording deferrals in the existing bounded event queue, backward-compatible with stored v1 data.
- Activates only newly accepted events and never re-arms unrelated deferred backlog when a fresh notification arrives.
- Limits automatic startup recovery to the newest non-deferred Android event no more than 10 minutes old; all other pre-existing events are retained and persistently deferred without contacting the camera.
- Adds secret-safe backlog-policy diagnostics and deterministic regression coverage.
- Keeps the 60-second settle delay, shared local-operation lock, recording fingerprint deduplication, ready-event contract and verified cmd13/cmd8 transport unchanged.

## Earlier stable releases

### v1.2.1 — Recording deduplication
- Prevents duplicate Home Assistant/Telegram delivery when multiple Android Reolink notifications map to the same physical SD-card clip.
- Stores a bounded, persistent SHA-256 recording fingerprint derived from the camera UID plus FileInfo identity, start/end timestamps and size. Raw filenames/record IDs are not exposed in diagnostics.
- Checks the persistent fingerprint set after FileInfo selection and before cmd13/cmd8, so duplicate events are completed silently without downloading the same MP4 again or firing a second `reolink_battery_recording_ready` event.
- Adds secret-safe dedupe diagnostics (`completed_recording_count`, `deduplicated_recordings`, last duplicate presence/time and policy).
- Keeps the v1.2.0 / beta45 transport, heartbeat, ACK, retry and verified-file behavior unchanged.
- `v0.1.2-beta.45` remains unchanged as the diagnostic transport reference.

### v1.2.0 — Stable
- First official stable release of the automatic Reolink battery-camera recording workflow.
- Promoted directly from the validated `v0.1.2-beta.45` codebase with no protocol or transport behavior changes.
- Verified end-to-end flow: Android/Reolink motion notification → Home Assistant queue → battery-camera wake/auth → FileInfo lookup → cmd13/cmd8 download → exact MP4 verification → Home Assistant recording-ready event → optional Telegram handoff.
- Uses the authoritative recording size reported by accepted cmd13 to resize the verified download collector before cmd8, allowing verified recordings larger than the former 16 MiB ceiling while retaining the 128 MiB hard cap.
- Keeps newest-first queue processing, bounded retry recovery, fresh-event retry preemption, persistent pending events, exact-size MP4 verification and atomic finalization.
- `v0.1.2-beta.45` remains available as a diagnostic prerelease/reference baseline if deeper troubleshooting is required.

## Retained diagnostic beta releases

### v0.1.2-beta.45 — Milestone 3B.19
- Stable `v1.2.0` is based directly on this tested code path.
- Fixes the remaining 16 MiB verified-download collector stop by resizing the verified collector from the authoritative size reported in the accepted cmd13 response before cmd8 begins.
- Keeps the 128 MiB verified-file hard cap and the 16 MiB manual diagnostic-probe cap.

### v0.1.2-beta.44 — Milestone 3B.18
- First size-aware verified collector attempt.
- Demonstrated that FileInfo candidate size is not reliable enough on the tested Argus 2E; candidate size could remain zero and leave the collector at the legacy 16 MiB diagnostic ceiling.

### v0.1.2-beta.43 — Milestone 3B.17
- Added fresh-motion retry preemption so a newer Android motion can interrupt an older event's 30/60-second retry wait without cancelling an active camera/download session.
- Added prior-attempt telemetry so the next attempt does not erase the previous failure stage, UID timing, stream termination reason, bytes received, expected size, or disconnect state.

## Historical milestones preserved in Git history

### v0.1.2-beta.41 — Milestone 3B.15
- Extended LAN UID discovery to 15 seconds with 500 ms resend cadence and added secret-safe discovery telemetry.

### v0.1.2-beta.40 — Transport baseline
- Established the proven post-auth fresh heartbeat TID behavior, 1-second heartbeat, 10 ms periodic-only inclusive-highest ACK behavior, reliable cmd13/cmd8 delivery, full-high/mainStream routing, exact-size MP4 verification, and successful Telegram end-to-end delivery.

## Release policy
- `v1.3.13` is the next stable patch release prepared from the v1.3.12 field-tested baseline.
- Every version bump must include matching root/archive checkpoints before release.
- Keep `v0.1.2-beta.45` available as a diagnostic reference; do not rewrite or retag it.
- Do not delete `recording_download_beta*.py` modules merely because they retain beta-era names; the stable path still inherits behavior from several of those modules.
- Refactor legacy beta-named modules only in a separate behavior-preserving cleanup after the stable release is proven.
