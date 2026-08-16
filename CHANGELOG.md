# Changelog

## Current validation baseline

### v0.1.2-beta.45 — Milestone 3B.19
- Current soak-test baseline.
- Verified end-to-end flow: Android/Reolink motion notification → Home Assistant queue → battery-camera wake/auth → FileInfo lookup → cmd13/cmd8 download → exact MP4 verification → Home Assistant recording-ready event → Telegram handoff.
- Fixes the remaining 16 MiB verified-download collector stop by resizing the verified collector from the authoritative size reported in the accepted cmd13 response before cmd8 begins.
- The observed 17,921,985-byte recording completed with a 22,116,289-byte collector limit.
- Keeps the 128 MiB verified-file hard cap and the 16 MiB manual diagnostic-probe cap.
- During soak testing, avoid protocol/transport changes unless diagnostics show a repeatable failure.

## Retained beta reference releases

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

## Release retention policy
- Keep only the three most recent `v0.1.2-beta.*` Releases/Tags while the project remains in beta.
- Do not rewrite Git history or remove historical commits.
- Do not delete `recording_download_beta*.py` modules merely because they are old beta names; the current production path still inherits behavior from several of those modules.
- After a multi-day successful soak test of beta.45, promote the same behavior to stable `v0.1.2` with no feature changes in that release.
- Refactor legacy beta-named modules only as a separate behavior-preserving cleanup after the stable release is proven.
