# Release Checkpoints

This directory contains immutable development handoff checkpoints for released integration versions.

## Policy

Every stable version bump must include all of the following in the same release change set:

1. Update `custom_components/reolink_battery/manifest.json`.
2. Update `CHANGELOG.md`.
3. Replace the root `CHECKPOINT.md` with a complete handoff for the new version.
4. Add an archive copy at `docs/checkpoints/v<version>.md`.
5. Keep the archive content byte-for-byte identical to the root checkpoint at release time.
6. Include current field evidence, design decisions, known failure signatures, next validation target, important files, and a new-chat kickoff prompt.
7. Do not publish a GitHub release when checkpoint validation fails.

The root `CHECKPOINT.md` is the current handoff document. Files in this directory are historical snapshots and should not be rewritten after their version is released.

## Required checkpoint header

The root/archive checkpoint must include a line in this form:

```text
**Checkpoint version:** vX.Y.Z
```

CI reads the integration version from `manifest.json`, verifies the root checkpoint version, requires the matching archive file, and verifies that the root and archive are identical.
