# Changelog

## Current stable release

### v1.2.0 — Stable
- First official stable release of the automatic Reolink battery-camera recording workflow.
- Promoted directly from the validated `v0.1.2-beta.45` codebase with no protocol or transport behavior changes.
- Verified end-to-end flow: Android/Reolink motion notification → Home Assistant queue → battery-camera wake/auth → FileInfo lookup → cmd13/cmd8 download → exact MP4 verification → Home Assistant recording-ready event → optional Telegram handoff.
- Uses the authoritative recording size reported by accepted cmd13 to resize the verified download collector before cmd8, allowing verified recordings larger than the former 16 MiB ceiling while retaining the 128 MiB hard cap.
- Keeps newest-first queue processing, bounded retry recovery, fresh-event retry preemption, persistent pending events, exact-size MP4 verification and atomic finalization.
- `v0.1.2-beta.45` remains available as the diagnostic prerelease/reference baseline if deeper troubleshooting is required.

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
- `v1.2.0` is the stable public release line.
- Keep `v0.1.2-beta.45` available as a diagnostic reference; do not rewrite or retag it.
- Do not delete `recording_download_beta*.py` modules merely because they retain beta-era names; the stable path still inherits behavior from several of those modules.
- Refactor legacy beta-named modules only in a separate behavior-preserving cleanup after the stable release is proven.
