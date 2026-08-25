"""Automatic worker recovery for incomplete Argus media streams.

This layer deliberately leaves the physically validated cmd13/cmd8, heartbeat,
UDP ACK and file-verification transport unchanged. It changes retry timing when
a real MP4 transfer ends incomplete and, from v1.3.10, gives recording attempts
priority over an active on-demand Live View session that owns the shared local
camera-operation lease.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime

from .recording_worker import (
    MAX_ATTEMPTS_PER_TRIGGER,
    RETRY_DELAYS_SECONDS,
    RecordingWorker as BaseRecordingWorker,
)

INCOMPLETE_STREAM_FAILURE_STAGE = "STREAM_REMOTE_DISCONNECT_INCOMPLETE"
INCOMPLETE_STREAM_IDLE_FAILURE_STAGE = "STREAM_IDLE_TIMEOUT_INCOMPLETE"
INCOMPLETE_STREAM_RETRY_DELAYS_SECONDS = (3.0, 6.0)


class RecordingWorker(BaseRecordingWorker):
    """Base worker plus battery-awake recovery and recording priority."""

    async def _process_once(self, event) -> bool:
        """Run one attempt after asking an active Live View session to yield.

        Live View and recording intentionally share one local-operation lock so
        two Baichuan sessions are never opened concurrently on the battery camera.
        A Live View consumer can legitimately remain connected for minutes,
        however, so simply waiting on that lock can make a fresh recording event
        stale before UID discovery even begins. The hub's recording-priority gate
        stops the current producer, blocks reconnects, lets the base worker take
        the existing lock normally, then re-enables queued Live View consumers.
        """
        runtime = self._entry.runtime_data
        live_hub = getattr(runtime, "live_hub", None)
        pause = getattr(live_hub, "async_pause_for_recording", None)
        resume = getattr(live_hub, "async_resume_after_recording", None)
        if not callable(pause) or not callable(resume):
            return await super()._process_once(event)

        await pause()
        try:
            return await super()._process_once(event)
        finally:
            # A Live View restart problem must never turn a completed verified
            # recording into a worker failure or prevent retry classification.
            with suppress(Exception):
                await resume()

    def _classify_incomplete_stream_failure(self) -> bool:
        """Promote a verified partial transfer stop to a distinct failure.

        Fast recovery is eligible only when media bytes were actually written,
        cmd13 reported an authoritative expected size, and fewer bytes were
        collected than expected. The stop must then be either an explicit remote
        connection close or a stream idle timeout after transfer progress.
        Authentication, FileInfo, routing and local filesystem failures keep the
        original retry timing.
        """
        from .recording_download_beta22 import stream_probe_state

        trace = stream_probe_state(self._entry.entry_id)
        file_bytes = int(getattr(trace, "file_bytes_written", 0) or 0)
        expected_size = int(getattr(trace, "xml_reported_size", 0) or 0)
        remote_disconnect = bool(
            getattr(trace, "remote_disconnect_observed", False)
        )
        termination_reason = str(
            getattr(trace, "termination_reason", "") or ""
        )

        partial = file_bytes > 0 and expected_size > file_bytes
        if not partial:
            return False

        if remote_disconnect and termination_reason == "connection_closed":
            self.state.last_failure_stage = INCOMPLETE_STREAM_FAILURE_STAGE
            self.state.last_failure_type = "remote_disconnect_before_expected_size"
            return True

        if termination_reason == "idle_timeout":
            self.state.last_failure_stage = INCOMPLETE_STREAM_IDLE_FAILURE_STAGE
            self.state.last_failure_type = "idle_timeout_before_expected_size"
            return True

        return False

    @staticmethod
    def _retry_delay(
        attempt: int,
        *,
        fast_recovery: bool,
        fast_retry_index: int,
    ) -> float:
        """Return the next battery-safe delay without changing attempt limits."""
        if fast_recovery:
            return INCOMPLETE_STREAM_RETRY_DELAYS_SECONDS[
                min(
                    fast_retry_index,
                    len(INCOMPLETE_STREAM_RETRY_DELAYS_SECONDS) - 1,
                )
            ]
        return RETRY_DELAYS_SECONDS[
            min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)
        ]

    async def _process_trigger(self) -> None:
        """Process one motion while preserving a partial-download wake window."""
        await self._defer_stale_activated_events(datetime.now(UTC))
        event = self._next_android_event()
        while event is not None and not self._stopped.is_set():
            if not await self._settle_event(event):
                return

            completed = False
            preempted = False
            fast_recovery = False
            fast_retry_index = 0

            for attempt in range(MAX_ATTEMPTS_PER_TRIGGER):
                if self._stopped.is_set():
                    return

                if attempt:
                    delay = self._retry_delay(
                        attempt,
                        fast_recovery=fast_recovery,
                        fast_retry_index=fast_retry_index,
                    )
                    if not await self._wait_retry_or_newer_event(event, delay):
                        if self._stopped.is_set():
                            return
                        preempted = True
                        break
                    self.state.retries += 1
                    if fast_recovery:
                        fast_retry_index += 1

                if await self._process_once(event):
                    completed = True
                    self._activated_event_ids.discard(event.event_id)
                    break

                # Once a real transfer is interrupted, keep all remaining retries
                # in the short recovery window even if the immediate reconnect
                # subsequently fails at wake/auth/FileInfo while the camera is
                # still transitioning from the interrupted transfer.
                if self._classify_incomplete_stream_failure():
                    fast_recovery = True

            if not completed and not preempted:
                await self._defer_event(event, "automatic_retries_exhausted")

            await self._defer_stale_activated_events(datetime.now(UTC))
            event = self._next_android_event()
