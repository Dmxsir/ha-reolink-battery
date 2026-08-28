"""Automatic worker recovery for incomplete Argus media streams.

This layer deliberately leaves the physically validated cmd13/cmd8, heartbeat,
UDP ACK and file-verification transport unchanged. It changes retry timing when
a real MP4 transfer ends incomplete, gives recording attempts priority over an
active on-demand Live View session, and provides explicit manual backlog recovery
without rearming stale automatic backlog.
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
MANUAL_STALE_MATCH_FAILURE_STAGE = "RECORDING_MATCH_ERROR"
MANUAL_STALE_MATCH_DEFER_REASON = "manual_stale_recording_match_miss"


class RecordingWorker(BaseRecordingWorker):
    """Base worker plus battery-awake recovery, priority and manual recovery."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._manual_recovery_event_ids: set[str] = set()
        self._manual_recovery_requests = 0
        self._manual_recovery_rearmed = 0
        self._manual_recovery_last_queued = 0
        self._manual_stale_match_single_attempts = 0

        # v1.3.14: a fresh automatic notification may arrive while the serialized
        # worker is already inside a long manual/backlog camera operation. The
        # normal 10-minute freshness clock must not make that accepted event
        # disappear before it receives its first worker attempt. This is runtime
        # admission credit only: it never rearms deferred backlog and is consumed
        # immediately when the first attempt starts.
        self._automatic_first_attempt_credit_event_ids: set[str] = set()
        self._automatic_first_attempt_credits_granted = 0
        self._automatic_late_first_attempts = 0
        self._last_automatic_late_first_attempt_event_time: datetime | None = None

    def notify(self, event_id: str) -> None:
        """Activate an event and preserve one first attempt if it is fresh now.

        The credit is intentionally created only by the normal automatic notify
        path while the event is fresh. Manual recovery adds IDs directly to its
        own set, so old backlog does not gain this automatic freshness bypass.
        """
        super().notify(event_id)
        now = datetime.now(UTC)
        coordinator = self._entry.runtime_data.coordinator
        event = next(
            (
                candidate
                for candidate in coordinator.pending_events
                if candidate.event_id == event_id
                and candidate.source == "android_notification"
            ),
            None,
        )
        if (
            event is not None
            and not coordinator.is_event_deferred(event.event_id)
            and self._is_fresh(event, now)
        ):
            self._automatic_first_attempt_credit_event_ids.add(event.event_id)

    async def async_request_manual_recovery(self) -> int:
        """Explicitly activate every pending Android event, including stale ones.

        This method is only called from the user-pressed recovery button. Normal
        startup and automatic motion processing keep the existing freshness and
        deferred-event rules. A manual request re-arms deferred pending events,
        marks all current Android events as explicit recovery work and wakes the
        serialized worker. Existing deduplication still prevents re-downloading a
        recording that has already been verified and remembered.
        """
        coordinator = self._entry.runtime_data.coordinator
        pending = [
            event
            for event in coordinator.pending_events
            if event.source == "android_notification"
        ]
        if not pending:
            self._manual_recovery_last_queued = 0
            return 0

        rearmed = 0
        for event in pending:
            if coordinator.is_event_deferred(event.event_id):
                if await coordinator.async_rearm_event(event.event_id):
                    rearmed += 1

        event_ids = {event.event_id for event in pending}
        self._manual_recovery_event_ids.update(event_ids)
        self._activated_event_ids.update(event_ids)
        self._manual_recovery_requests += 1
        self._manual_recovery_rearmed += rearmed
        self._manual_recovery_last_queued = len(event_ids)
        self.state.deferred_rearmed_count += rearmed
        self.state.pending_trigger = True
        self._trigger.set()
        self._refresh_pending_counts()
        return len(event_ids)

    def _next_android_event(self, now: datetime | None = None):
        """Return newest eligible manual/fresh/first-attempt-credit work."""
        now = now or datetime.now(UTC)
        coordinator = self._entry.runtime_data.coordinator
        candidates = (
            event
            for event in coordinator.pending_events
            if event.source == "android_notification"
            and not coordinator.is_event_deferred(event.event_id)
            and (
                event.event_id in self._manual_recovery_event_ids
                or event.event_id in self._automatic_first_attempt_credit_event_ids
                or (
                    event.event_id in self._activated_event_ids
                    and self._is_fresh(event, now)
                )
            )
        )
        return max(candidates, key=self._event_time, default=None)

    async def _defer_stale_activated_events(self, now: datetime) -> None:
        """Defer stale automatic work except a fresh-at-activation first attempt."""
        for event in self._entry.runtime_data.coordinator.pending_events:
            if (
                event.event_id in self._activated_event_ids
                and event.event_id not in self._manual_recovery_event_ids
                and event.event_id
                not in self._automatic_first_attempt_credit_event_ids
                and not self._is_fresh(event, now)
            ):
                await self._defer_event(event, "automatic_event_stale")
        self._refresh_pending_counts(now)

    async def _defer_event(self, event, reason: str) -> None:
        await super()._defer_event(event, reason)
        self._manual_recovery_event_ids.discard(event.event_id)
        self._automatic_first_attempt_credit_event_ids.discard(event.event_id)

    def _consume_automatic_first_attempt_credit(
        self,
        event,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Consume runtime credit immediately before the event's first attempt."""
        if event.event_id not in self._automatic_first_attempt_credit_event_ids:
            return False

        self._automatic_first_attempt_credit_event_ids.discard(event.event_id)
        self._automatic_first_attempt_credits_granted += 1
        now = now or datetime.now(UTC)
        if not self._is_fresh(event, now):
            self._automatic_late_first_attempts += 1
            self._last_automatic_late_first_attempt_event_time = self._event_time(event)
        return True

    def _manual_stale_match_is_terminal(self, event) -> bool:
        """Return True for an old explicit recovery event with no SD match.

        A stale manual event has already waited far beyond the recording settle
        window. If FileInfo cannot map that old notification to an SD recording,
        waiting another 30/60 seconds cannot make that historical clip appear.
        Fresh/manual events keep normal retries because the recording may still be
        finalizing, and transient wake/auth/UID failures also keep normal retries.
        """
        return (
            event.event_id in self._manual_recovery_event_ids
            and not self._is_fresh(event, datetime.now(UTC))
            and self.state.last_failure_stage == MANUAL_STALE_MATCH_FAILURE_STAGE
        )

    def policy_diagnostics(self) -> dict[str, object]:
        """Extend base policy diagnostics with explicit recovery/admission state."""
        data = super().policy_diagnostics()
        pending_ids = {
            event.event_id
            for event in self._entry.runtime_data.coordinator.pending_events
            if event.source == "android_notification"
        }
        data.update(
            {
                "manual_recovery_requests": self._manual_recovery_requests,
                "manual_recovery_last_queued": self._manual_recovery_last_queued,
                "manual_recovery_rearmed": self._manual_recovery_rearmed,
                "manual_recovery_remaining": len(
                    pending_ids.intersection(self._manual_recovery_event_ids)
                ),
                "manual_recovery_policy": "explicit_button_all_pending",
                "manual_stale_match_single_attempts": self._manual_stale_match_single_attempts,
                "manual_stale_match_retry_policy": "single_attempt_then_defer",
                "automatic_first_attempt_credit_pending": len(
                    pending_ids.intersection(
                        self._automatic_first_attempt_credit_event_ids
                    )
                ),
                "automatic_first_attempt_credits_granted": (
                    self._automatic_first_attempt_credits_granted
                ),
                "automatic_late_first_attempts": self._automatic_late_first_attempts,
                "last_automatic_late_first_attempt_event_time": (
                    self._last_automatic_late_first_attempt_event_time.isoformat()
                    if self._last_automatic_late_first_attempt_event_time is not None
                    else None
                ),
                "automatic_first_attempt_credit_policy": (
                    "fresh_at_activation_until_first_attempt"
                ),
            }
        )
        return data

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
        """Process motion/manual work while preserving partial-download recovery."""
        await self._defer_stale_activated_events(datetime.now(UTC))
        event = self._next_android_event()
        while event is not None and not self._stopped.is_set():
            if not await self._settle_event(event):
                return

            completed = False
            preempted = False
            fast_recovery = False
            fast_retry_index = 0
            defer_reason = "automatic_retries_exhausted"

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

                # Consume only when a real worker attempt is about to start. If
                # this event became >10 minutes old solely because serialized
                # manual/backlog work held the worker, it still gets this attempt.
                if attempt == 0:
                    self._consume_automatic_first_attempt_credit(event)

                if await self._process_once(event):
                    completed = True
                    self._activated_event_ids.discard(event.event_id)
                    self._manual_recovery_event_ids.discard(event.event_id)
                    self._automatic_first_attempt_credit_event_ids.discard(
                        event.event_id
                    )
                    break

                # Once a real transfer is interrupted, keep all remaining retries
                # in the short recovery window even if the immediate reconnect
                # subsequently fails at wake/auth/FileInfo while the camera is
                # still transitioning from the interrupted transfer.
                if self._classify_incomplete_stream_failure():
                    fast_recovery = True

                # Old explicit backlog is different from a fresh recording race:
                # once FileInfo says there is no matching historical recording,
                # another 30/60-second retry only burns time and battery. Keep
                # retries for UID/auth/transport and for any still-fresh event.
                if self._manual_stale_match_is_terminal(event):
                    self._manual_stale_match_single_attempts += 1
                    defer_reason = MANUAL_STALE_MATCH_DEFER_REASON
                    break

            if not completed and not preempted:
                await self._defer_event(event, defer_reason)

            await self._defer_stale_activated_events(datetime.now(UTC))
            event = self._next_android_event()
