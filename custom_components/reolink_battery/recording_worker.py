"""Automatic camera-closed settle -> verified recording download worker.

The worker is deliberately event-driven.  It never polls the sleeping camera.
A newly queued Android notification wakes only this in-memory worker; the worker
waits for recording finalization with no camera session open, then reuses the
physically validated beta.22 cmd13 -> handle-bound cmd8 full-high path.  A queue
item is removed only after an atomically finalized MP4 is present on disk.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .const import (
    CONF_DEVICE_PASSWORD,
    CONF_DEVICE_USERNAME,
    CONF_INTERFACE,
    CONF_TELEGRAM_NOTIFY_ENTITY,
    CONF_UID,
)

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
    last_event_time: datetime | None = None
    last_attempt_time: datetime | None = None
    last_completed_time: datetime | None = None
    last_failure_stage: str = ""
    last_failure_type: str = ""
    last_file_saved: bool = False
    last_file_size: int = 0
    last_ready_event_fired: bool = False
    waiting_camera_closed: bool = False
    telegram_configured: bool = False
    telegram_send_attempted: bool = False
    telegram_send_succeeded: bool = False
    telegram_path_allowed: bool | None = None
    telegram_service_available: bool | None = None
    telegram_last_failure_stage: str = ""
    telegram_last_failure_type: str = ""
    telegram_last_send_time: datetime | None = None


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
        self.state = RecordingWorkerState()

    def notify(self) -> None:
        """Signal that the persistent queue may contain work."""
        self.state.pending_trigger = True
        self._trigger.set()

    async def async_shutdown(self) -> None:
        """Stop waits promptly; config-entry task cancellation remains safe."""
        self.state.enabled = False
        self._stopped.set()
        self._trigger.set()

    def _oldest_android_event(self) -> CloudEvent | None:
        return next(
            (
                event
                for event in self._entry.runtime_data.coordinator.pending_events
                if event.source == "android_notification"
            ),
            None,
        )

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

    async def _async_send_telegram(self, final_path: Path, event: CloudEvent) -> None:
        """Send one already-verified MP4 through Home Assistant Telegram Bot."""
        entity_id = self._entry.options.get(CONF_TELEGRAM_NOTIFY_ENTITY)
        state = self.state
        state.telegram_configured = isinstance(entity_id, str) and bool(entity_id)
        state.telegram_send_attempted = False
        state.telegram_send_succeeded = False
        state.telegram_path_allowed = None
        state.telegram_service_available = None
        state.telegram_last_failure_stage = ""
        state.telegram_last_failure_type = ""
        if not state.telegram_configured:
            return

        state.telegram_send_attempted = True
        path = str(final_path)
        state.telegram_path_allowed = self._hass.config.is_allowed_path(path)
        if not state.telegram_path_allowed:
            state.telegram_last_failure_stage = "TELEGRAM_FILE_PATH_NOT_ALLOWED"
            return

        state.telegram_service_available = self._hass.services.has_service(
            "telegram_bot", "send_video"
        )
        if not state.telegram_service_available:
            state.telegram_last_failure_stage = "TELEGRAM_SERVICE_UNAVAILABLE"
            return

        camera_name = event.device_name or self._entry.title
        try:
            await self._hass.services.async_call(
                "telegram_bot",
                "send_video",
                {
                    "entity_id": entity_id,
                    "file": path,
                    "caption": f"🎥 תנועה זוהתה במצלמה {camera_name}",
                    "parse_mode": "plain_text",
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - delivery must not invalidate recording.
            state.telegram_last_failure_stage = "TELEGRAM_SEND_ERROR"
            state.telegram_last_failure_type = type(err).__name__
            return
        state.telegram_send_succeeded = True
        state.telegram_last_send_time = datetime.now(UTC)

    async def _process_once(self, event: CloudEvent) -> bool:
        """Run one closed-on-entry session and commit only a verified MP4."""
        # Keep protocol-heavy imports lazy: normal HA startup never contacts the
        # camera and does not need to initialize this path until work exists.
        from .camera import CameraStageError
        from .recording_download_beta22 import (
            apply_stream_probe_trace,
            async_prepare_download_for_event,
        )

        self.state.attempts += 1
        self.state.last_attempt_time = datetime.now(UTC)
        self.state.last_event_time = event.notification_post_time or event.alarm_time
        self.state.last_failure_stage = ""
        self.state.last_failure_type = ""
        self.state.last_file_saved = False
        self.state.last_file_size = 0
        self.state.last_ready_event_fired = False

        output_dir = Path(self._hass.config.path("reolink_battery", "recordings"))
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
                )
        except CameraStageError as err:
            apply_stream_probe_trace(
                self._entry.entry_id, getattr(err, "stream_trace", None)
            )
            self.state.last_failure_stage = err.stage
            self.state.last_failure_type = type(err).__name__
            return False
        except Exception as err:  # noqa: BLE001 - worker must preserve queue on failure.
            self.state.last_failure_stage = "RECORDING_WORKER_ERROR"
            self.state.last_failure_type = type(err).__name__
            return False

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

        # Persistent queue completion happens only after disk verification.
        await self._entry.runtime_data.coordinator.async_complete_event(event.event_id)
        self.state.completed += 1
        self.state.last_completed_time = datetime.now(UTC)
        self.state.last_file_saved = True
        self.state.last_file_size = expected_size

        self._hass.bus.async_fire(
            RECORDING_READY_EVENT,
            {
                "config_entry_id": self._entry.entry_id,
                "device_name": event.device_name,
                "event_id": event.event_id,
                "alarm_time": event.alarm_time.isoformat(),
                "source": event.source,
                "file_path": str(final_path),
                "file_size": expected_size,
            },
        )
        self.state.last_ready_event_fired = True
        await self._async_send_telegram(final_path, event)
        return True

    async def _process_trigger(self) -> None:
        event = self._oldest_android_event()
        while event is not None and not self._stopped.is_set():
            if not await self._settle_event(event):
                return

            completed = False
            for attempt in range(MAX_ATTEMPTS_PER_TRIGGER):
                if self._stopped.is_set():
                    return
                if attempt:
                    self.state.retries += 1
                    delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
                    if not await self._wait_camera_closed(delay):
                        return
                if await self._process_once(event):
                    completed = True
                    break

            if not completed:
                # Keep the event pending.  Do not loop forever and repeatedly
                # wake a battery camera; a later notification/reload can trigger
                # another bounded attempt set.
                return
            event = self._oldest_android_event()

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
