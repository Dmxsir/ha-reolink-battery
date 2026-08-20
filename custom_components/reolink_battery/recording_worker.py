"""Automatic camera-closed settle -> verified recording download worker.

The worker is deliberately event-driven. It never polls the sleeping camera.
A newly queued Android notification wakes only this in-memory worker; the worker
waits for recording finalization with no camera session open, then reuses the
physically validated beta.22 cmd13 -> handle-bound cmd8 full-high path. A queue
item is removed only after an atomically finalized MP4 is present on disk.

Events that exhaust their bounded retry set are persistently deferred so they
cannot block newer motion events or wake the camera again after a restart.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .const import (
    AUTOMATIC_RECORDING_EVENT_MAX_AGE,
    CONF_DEVICE_PASSWORD,
    CONF_DEVICE_USERNAME,
    CONF_INTERFACE,
    CONF_UID,
)
from .events import is_automatic_event_fresh

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import ReolinkBatteryConfigEntry
    from .events import CloudEvent

RECORDING_READY_EVENT = "reolink_battery_recording_ready"
RECORDING_SETTLE_SECONDS = 60.0
MAX_ATTEMPTS_PER_TRIGGER = 3
RETRY_DELAYS_SECONDS = (30.0, 60.0)


@dataclass(slots=True)
class RecordingWorkerState:
    """Secret-safe runtime telemetry for the automatic worker."""

    enabled: bool = True
    running: bool = False
    pending_trigger: bool = False
    settle_seconds: float = RECORDING_SETTLE_SECONDS
    attempts: int = 0
    retries: int = 0
    completed: int = 0
    deduplicated_recordings: int = 0
    last_duplicate_event_time: datetime | None = None
    last_duplicate_recording_fingerprint_present: bool = False
    deferred_count: int = 0
    deferred_rearmed_count: int = 0
    eligible_fresh_pending_count: int = 0
    stale_pending_count: int = 0
    startup_recovery_eligible: bool = False
    startup_skipped_stale_count: int = 0
    last_startup_skipped_event_time: datetime | None = None
    retry_preemptions: int = 0
    last_preempted_event_time: datetime | None = None
    last_preempting_event_time: datetime | None = None
    prior_attempt_event_time: datetime | None = None
    prior_attempt_time: datetime | None = None
    prior_failure_stage: str = ""
    prior_failure_type: str = ""
    prior_uid_resolve_succeeded: bool = False
    prior_uid_resolve_elapsed_ms: float | None = None
    prior_stream_termination_reason: str = ""
    prior_stream_elapsed_seconds: float | None = None
    prior_stream_file_bytes: int = 0
    prior_stream_expected_size: int | None = None
    prior_stream_remote_disconnect: bool = False
    last_event_time: datetime | None = None
    last_attempt_time: datetime | None = None
    last_completed_time: datetime | None = None
    last_deferred_event_time: datetime | None = None
    last_failure_stage: str = ""
    last_failure_type: str = ""
    last_file_saved: bool = False
    last_file_size: int = 0
    last_ready_event_fired: bool = False
    waiting_camera_closed: bool = False
    last_media_source_id: str = ""
    last_media_content_id_present: bool = False
    last_uid_resolve_timeout_seconds: float = 0.0
    last_uid_resolve_resend_interval_seconds: float = 0.0
    last_uid_resolve_send_rounds: int = 0
    last_uid_resolve_datagrams_sent: int = 0
    last_uid_resolve_elapsed_ms: float | None = None
    last_uid_resolve_succeeded: bool = False


class RecordingWorker:
    """One serialized automatic recording worker for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ReolinkBatteryConfigEntry,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._trigger = asyncio.Event()
        self._stopped = asyncio.Event()
        self._activated_event_ids: set[str] = set()
        self.state = RecordingWorkerState()
        self.state.deferred_count = (
            self._entry.runtime_data.coordinator.deferred_event_count
        )

    def notify(self, event_id: str) -> None:
        """Activate one accepted event without re-arming unrelated backlog."""
        self._activated_event_ids.add(event_id)
        self.state.pending_trigger = True
        self._trigger.set()

    async def async_shutdown(self) -> None:
        """Stop waits promptly; config-entry task cancellation remains safe."""
        self.state.enabled = False
        self._stopped.set()
        self._trigger.set()

    @staticmethod
    def _event_time(event: CloudEvent) -> datetime:
        return event.notification_post_time or event.alarm_time

    @classmethod
    def _is_fresh(cls, event: CloudEvent, now: datetime) -> bool:
        return is_automatic_event_fresh(event, now)

    def _eligible_pending_events(self, now: datetime) -> list[CloudEvent]:
        coordinator = self._entry.runtime_data.coordinator
        return [
            event
            for event in coordinator.pending_events
            if event.source == "android_notification"
            and not coordinator.is_event_deferred(event.event_id)
            and self._is_fresh(event, now)
        ]

    def _refresh_pending_counts(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        coordinator = self._entry.runtime_data.coordinator
        android_pending = [
            event
            for event in coordinator.pending_events
            if event.source == "android_notification"
        ]
        self.state.deferred_count = coordinator.deferred_event_count
        self.state.eligible_fresh_pending_count = len(
            self._eligible_pending_events(now)
        )
        self.state.stale_pending_count = sum(
            not self._is_fresh(event, now) for event in android_pending
        )

    def _next_android_event(self, now: datetime | None = None) -> CloudEvent | None:
        """Return newest activated, fresh and non-deferred Android event.

        A fresh motion notification must not wait behind stale backlog: the camera
        is naturally awake around the fresh event, so newest-first maximizes the
        chance of completing that recording before deep sleep. Older pending
        events remain persistent for diagnosis or explicit future recovery.
        """
        now = now or datetime.now(UTC)
        coordinator = self._entry.runtime_data.coordinator
        candidates = (
            event
            for event in coordinator.pending_events
            if event.source == "android_notification"
            and event.event_id in self._activated_event_ids
            and not coordinator.is_event_deferred(event.event_id)
            and self._is_fresh(event, now)
        )
        return max(
            candidates,
            key=lambda event: event.notification_post_time or event.alarm_time,
            default=None,
        )

    async def _defer_event(self, event: CloudEvent, reason: str) -> None:
        """Persistently defer a failed/stale event without deleting it."""
        await self._entry.runtime_data.coordinator.async_defer_event(
            event.event_id,
            reason,
        )
        self._activated_event_ids.discard(event.event_id)
        self.state.deferred_count = (
            self._entry.runtime_data.coordinator.deferred_event_count
        )
        self.state.last_deferred_event_time = (
            event.notification_post_time or event.alarm_time
        )

    async def async_prepare_startup_recovery(
        self, *, now: datetime | None = None
    ) -> CloudEvent | None:
        """Classify startup backlog and activate at most its newest fresh event."""
        now = now or datetime.now(UTC)
        coordinator = self._entry.runtime_data.coordinator
        android_pending = [
            event
            for event in coordinator.pending_events
            if event.source == "android_notification"
        ]
        stale = [event for event in android_pending if not self._is_fresh(event, now)]
        self.state.startup_skipped_stale_count = len(stale)
        self.state.last_startup_skipped_event_time = (
            max((self._event_time(event) for event in stale), default=None)
        )
        for event in stale:
            if not coordinator.is_event_deferred(event.event_id):
                await coordinator.async_defer_event(
                    event.event_id,
                    "startup_event_stale",
                    deferred_at=now,
                )

        eligible = self._eligible_pending_events(now)
        candidate = max(eligible, key=self._event_time, default=None)
        self.state.startup_recovery_eligible = candidate is not None
        self._refresh_pending_counts(now)
        if candidate is not None:
            self.notify(candidate.event_id)
        return candidate

    async def _defer_stale_activated_events(self, now: datetime) -> None:
        for event in self._entry.runtime_data.coordinator.pending_events:
            if (
                event.event_id in self._activated_event_ids
                and not self._is_fresh(event, now)
            ):
                await self._defer_event(event, "automatic_event_stale")
        self._refresh_pending_counts(now)

    def policy_diagnostics(self) -> dict[str, object]:
        """Return secret-safe automatic processing policy state."""
        self._refresh_pending_counts()
        coordinator = self._entry.runtime_data.coordinator
        last_deferred = coordinator.last_deferred_event
        last_deferred_pending = next(
            (
                event
                for event in coordinator.pending_events
                if last_deferred is not None
                and event.event_id == last_deferred.event_id
            ),
            None,
        )
        return {
            "persistent_deferred_count": coordinator.deferred_event_count,
            "eligible_fresh_pending_count": self.state.eligible_fresh_pending_count,
            "stale_pending_count": self.state.stale_pending_count,
            "startup_recovery_eligible": self.state.startup_recovery_eligible,
            "startup_skipped_stale_count": self.state.startup_skipped_stale_count,
            "last_deferred_event_time": (
                self._event_time(last_deferred_pending).isoformat()
                if last_deferred_pending is not None
                else None
            ),
            "last_deferred_reason": (
                last_deferred.reason if last_deferred is not None else None
            ),
            "last_startup_skipped_event_time": (
                self.state.last_startup_skipped_event_time.isoformat()
                if self.state.last_startup_skipped_event_time is not None
                else None
            ),
            "automatic_event_max_age_seconds": int(
                AUTOMATIC_RECORDING_EVENT_MAX_AGE.total_seconds()
            ),
        }

    def _newer_pending_event(self, event: CloudEvent) -> CloudEvent | None:
        """Return a newer non-deferred Android event, if one is queued."""
        newest = self._next_android_event()
        if newest is None or newest.event_id == event.event_id:
            return None
        if self._event_time(newest) <= self._event_time(event):
            return None
        return newest

    def _record_retry_preemption(
        self, event: CloudEvent, newer: CloudEvent
    ) -> None:
        self.state.retry_preemptions += 1
        self.state.last_preempted_event_time = self._event_time(event)
        self.state.last_preempting_event_time = self._event_time(newer)

    async def _wait_retry_or_newer_event(
        self, event: CloudEvent, seconds: float
    ) -> bool:
        """Wait for retry delay, but yield immediately to a newer motion.

        Returns True only when the full retry delay elapsed. A False result means
        shutdown or a newer queued motion should take priority. No camera session
        is opened by this wait.
        """
        newer = self._newer_pending_event(event)
        if newer is not None:
            self._record_retry_preemption(event, newer)
            return False
        if seconds <= 0:
            return not self._stopped.is_set()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        self.state.waiting_camera_closed = True
        try:
            while not self._stopped.is_set():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return True
                try:
                    await asyncio.wait_for(self._trigger.wait(), timeout=remaining)
                except TimeoutError:
                    return True
                if self._stopped.is_set():
                    return False
                self._trigger.clear()
                self.state.pending_trigger = False
                newer = self._newer_pending_event(event)
                if newer is not None:
                    self._record_retry_preemption(event, newer)
                    return False
            return False
        finally:
            self.state.waiting_camera_closed = False

    async def _wait_camera_closed(self, seconds: float) -> bool:
        """Wait without opening any camera transport. Return False if stopping."""
        if seconds <= 0:
            return not self._stopped.is_set()
        self.state.waiting_camera_closed = True
        try:
            await asyncio.wait_for(self._stopped.wait(), timeout=seconds)
            return False
        except TimeoutError:
            return True
        finally:
            self.state.waiting_camera_closed = False

    async def _settle_event(self, event: CloudEvent) -> bool:
        event_time = event.notification_post_time or event.alarm_time
        age = (datetime.now(UTC) - event_time.astimezone(UTC)).total_seconds()
        return await self._wait_camera_closed(max(RECORDING_SETTLE_SECONDS - age, 0.0))

    def _apply_uid_resolve_trace(self, trace) -> None:
        """Copy only secret-safe UID wake timing/cadence telemetry."""
        if trace is None:
            return
        self.state.last_uid_resolve_timeout_seconds = float(
            getattr(trace, "timeout_seconds", 0.0) or 0.0
        )
        self.state.last_uid_resolve_resend_interval_seconds = float(
            getattr(trace, "resend_interval_seconds", 0.0) or 0.0
        )
        self.state.last_uid_resolve_send_rounds = int(
            getattr(trace, "send_rounds", 0) or 0
        )
        self.state.last_uid_resolve_datagrams_sent = int(
            getattr(trace, "datagrams_sent", 0) or 0
        )
        self.state.last_uid_resolve_elapsed_ms = getattr(trace, "elapsed_ms", None)
        self.state.last_uid_resolve_succeeded = bool(
            getattr(trace, "succeeded", False)
        )

    async def _process_once(self, event: CloudEvent) -> bool:
        """Run one closed-on-entry session and commit only a verified MP4."""
        # Keep protocol-heavy imports lazy: normal HA startup never contacts the
        # camera and does not need to initialize this path until work exists.
        from .camera import CameraStageError
        from .events import CompletedRecording
        from .recording_download_beta22 import (
            apply_stream_probe_trace,
            async_prepare_download_for_event,
            reset_stream_probe_state,
            stream_probe_state,
        )
        from .recording_download_probe import RecordingAlreadyCompletedError

        if self.state.last_attempt_time is not None:
            prior_trace = stream_probe_state(self._entry.entry_id)
            self.state.prior_attempt_event_time = self.state.last_event_time
            self.state.prior_attempt_time = self.state.last_attempt_time
            self.state.prior_failure_stage = self.state.last_failure_stage
            self.state.prior_failure_type = self.state.last_failure_type
            self.state.prior_uid_resolve_succeeded = (
                self.state.last_uid_resolve_succeeded
            )
            self.state.prior_uid_resolve_elapsed_ms = (
                self.state.last_uid_resolve_elapsed_ms
            )
            self.state.prior_stream_termination_reason = str(
                getattr(prior_trace, "termination_reason", "") or ""
            )
            self.state.prior_stream_elapsed_seconds = getattr(
                prior_trace, "elapsed_seconds", None
            )
            self.state.prior_stream_file_bytes = int(
                getattr(prior_trace, "file_bytes_written", 0) or 0
            )
            self.state.prior_stream_expected_size = getattr(
                prior_trace, "xml_reported_size", None
            )
            self.state.prior_stream_remote_disconnect = bool(
                getattr(prior_trace, "remote_disconnect_observed", False)
            )

        self.state.attempts += 1
        self.state.last_attempt_time = datetime.now(UTC)
        self.state.last_event_time = event.notification_post_time or event.alarm_time
        self.state.last_failure_stage = ""
        self.state.last_failure_type = ""
        self.state.last_file_saved = False
        self.state.last_file_size = 0
        self.state.last_ready_event_fired = False
        self.state.last_duplicate_recording_fingerprint_present = False
        self.state.last_uid_resolve_timeout_seconds = 0.0
        self.state.last_uid_resolve_resend_interval_seconds = 0.0
        self.state.last_uid_resolve_send_rounds = 0
        self.state.last_uid_resolve_datagrams_sent = 0
        self.state.last_uid_resolve_elapsed_ms = None
        self.state.last_uid_resolve_succeeded = False

        # Do not let a previous partial cmd8 attempt leak into diagnostics for a
        # later failure that occurs before cmd13/cmd8 (for example cmd14 open).
        event_time = event.notification_post_time or event.alarm_time
        reset_stream_probe_state(
            self._entry.entry_id,
            telemetry_owner="worker",
            telemetry_event_time=event_time,
        )

        media_dirs = self._hass.config.media_dirs
        if not media_dirs:
            self.state.last_failure_stage = "MEDIA_DIRECTORY_UNAVAILABLE"
            return False
        media_source_id = "local" if "local" in media_dirs else next(iter(media_dirs))
        output_dir = Path(media_dirs[media_source_id]) / "reolink_battery"
        self.state.last_media_source_id = media_source_id
        self.state.last_media_content_id_present = False
        try:
            async with self._entry.runtime_data.local_operation_lock:
                result = await async_prepare_download_for_event(
                    event,
                    self._entry.data[CONF_UID],
                    self._entry.data[CONF_DEVICE_USERNAME],
                    self._entry.data[CONF_DEVICE_PASSWORD],
                    ipaddress.ip_interface(self._entry.data[CONF_INTERFACE]),
                    self._hass.config.time_zone,
                    output_dir=str(output_dir),
                    telemetry_owner="worker",
                    telemetry_event_time=event_time,
                    completed_recording_fingerprints=frozenset(
                        self._entry.runtime_data.coordinator.completed_recording_fingerprints
                    ),
                )
        except RecordingAlreadyCompletedError as err:
            self._apply_uid_resolve_trace(err.uid_resolve_trace)
            await self._entry.runtime_data.coordinator.async_complete_event(event.event_id)
            self.state.deduplicated_recordings += 1
            self.state.last_duplicate_event_time = event_time
            self.state.last_duplicate_recording_fingerprint_present = bool(
                err.fingerprint
            )
            self.state.last_failure_stage = ""
            self.state.last_failure_type = ""
            return True
        except CameraStageError as err:
            self._apply_uid_resolve_trace(getattr(err, "uid_resolve_trace", None))
            apply_stream_probe_trace(
                self._entry.entry_id, getattr(err, "stream_trace", None)
            )
            self.state.last_failure_stage = err.stage
            failure_type = str(getattr(err, "failure_type", "") or type(err).__name__)
            response_code = getattr(err, "response_code", None)
            if isinstance(response_code, int):
                failure_type = f"{failure_type}:rsp{response_code}"
            self.state.last_failure_type = failure_type
            return False
        except Exception as err:  # noqa: BLE001 - worker must preserve queue on failure.
            self.state.last_failure_stage = "RECORDING_WORKER_ERROR"
            self.state.last_failure_type = type(err).__name__
            return False

        self._apply_uid_resolve_trace(getattr(result, "uid_resolve_trace", None))
        trace = result.stream_trace
        apply_stream_probe_trace(self._entry.entry_id, trace)
        if not getattr(trace, "file_saved", False):
            self.state.last_failure_stage = "RECORDING_FILE_NOT_VERIFIED"
            return False

        candidate_start = result.candidate_start
        final_path = output_dir / f"reolink_{candidate_start.strftime('%Y%m%d_%H%M%S')}.mp4"
        expected_size = int(getattr(trace, "final_size", 0) or 0)
        try:
            file_ok = (
                expected_size > 0
                and final_path.is_file()
                and final_path.stat().st_size == expected_size
            )
        except OSError as err:
            self.state.last_failure_stage = "RECORDING_FILE_VERIFY_ERROR"
            self.state.last_failure_type = type(err).__name__
            return False
        if not file_ok:
            self.state.last_failure_stage = "RECORDING_FILE_VERIFY_ERROR"
            return False

        # Persist the recording fingerprint before publishing the ready event.
        # A later notification that maps to this exact FileInfo recording is
        # completed silently before cmd13/cmd8, preventing duplicate Telegram sends.
        await self._entry.runtime_data.coordinator.async_complete_event(
            event.event_id,
            completed_recording=CompletedRecording(
                fingerprint=result.candidate_fingerprint,
                start_time=result.candidate_start,
                end_time=result.candidate_end,
                size=expected_size,
            ),
        )
        self.state.completed += 1
        self.state.last_completed_time = datetime.now(UTC)
        self.state.last_file_saved = True
        self.state.last_file_size = expected_size

        media_content_id = (
            f"media-source://media_source/{media_source_id}/"
            f"reolink_battery/{final_path.name}"
        )
        self.state.last_media_content_id_present = True

        self._hass.bus.async_fire(
            RECORDING_READY_EVENT,
            {
                "config_entry_id": self._entry.entry_id,
                "device_name": event.device_name,
                "event_id": event.event_id,
                "alarm_time": event.alarm_time.isoformat(),
                "source": event.source,
                "file_path": str(final_path),
                "media_content_id": media_content_id,
                "file_size": expected_size,
            },
        )
        self.state.last_ready_event_fired = True
        return True

    async def _process_trigger(self) -> None:
        await self._defer_stale_activated_events(datetime.now(UTC))
        event = self._next_android_event()
        while event is not None and not self._stopped.is_set():
            if not await self._settle_event(event):
                return

            completed = False
            preempted = False
            for attempt in range(MAX_ATTEMPTS_PER_TRIGGER):
                if self._stopped.is_set():
                    return
                if attempt:
                    delay = RETRY_DELAYS_SECONDS[
                        min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)
                    ]
                    if not await self._wait_retry_or_newer_event(event, delay):
                        if self._stopped.is_set():
                            return
                        preempted = True
                        break
                    self.state.retries += 1
                if await self._process_once(event):
                    completed = True
                    self._activated_event_ids.discard(event.event_id)
                    break

            if not completed and not preempted:
                await self._defer_event(event, "automatic_retries_exhausted")

            # Continue newest-first only through explicitly activated fresh work.
            await self._defer_stale_activated_events(datetime.now(UTC))
            event = self._next_android_event()

    async def async_run(self) -> None:
        """Consume trigger signals until config-entry unload."""
        while not self._stopped.is_set():
            await self._trigger.wait()
            self._trigger.clear()
            self.state.pending_trigger = False
            if self._stopped.is_set():
                break
            self.state.running = True
            try:
                await self._process_trigger()
            finally:
                self.state.running = False
