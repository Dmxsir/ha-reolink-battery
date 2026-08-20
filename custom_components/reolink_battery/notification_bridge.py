"""Home Assistant Companion notification bridge for legacy Reolink cameras."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import REOLINK_ANDROID_PACKAGE, REOLINK_NOTIFICATION_CHANNEL
from .events import CloudEvent, is_automatic_event_fresh, parse_alarm_time

_ENGLISH_ALARM = re.compile(r"^\s*An\s+alarm\s+from\s+(.+?)\.?\s*$", re.IGNORECASE)
_STALE_REPOST_MIN_AGE_SECONDS = 300.0
_STALE_REPOST_DEBOUNCE_SECONDS = 15.0


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split()).rstrip(".")


def _extract_camera_name(body: str) -> str | None:
    """Extract a camera name from known notification text templates."""
    match = _ENGLISH_ALARM.match(body)
    if match is None:
        return None
    name = match.group(1).strip().rstrip(".")
    return name or None


def notification_event_from_attributes(
    attributes: Mapping[str, Any], *, expected_device_name: str, uid: str
) -> CloudEvent | None:
    """Normalize one public Android notification into a queued camera event."""
    if _text(attributes.get("package")) != REOLINK_ANDROID_PACKAGE:
        return None

    title = _text(attributes.get("android.title"))
    text = _text(attributes.get("android.text"))
    big_text = _text(attributes.get("android.bigText"))
    body = big_text or text
    if not body:
        return None

    extracted_name = _extract_camera_name(body)
    expected = _normalized(expected_device_name)
    if extracted_name is not None:
        if _normalized(extracted_name) != expected:
            return None
        device_name = expected_device_name
    else:
        # Fallback for a future/localized template: package is mandatory, and
        # accept only when another Reolink alarm signal is present and the
        # configured camera name is visible in the notification text.
        channel = _text(attributes.get("channel_id"))
        looks_like_alarm = (
            _normalized(title) == "camera alert"
            or channel == REOLINK_NOTIFICATION_CHANNEL
        )
        if not looks_like_alarm or expected not in _normalized(body):
            return None
        device_name = expected_device_name

    post_time = attributes.get("post_time")
    if isinstance(post_time, str):
        try:
            post_time = int(post_time)
        except ValueError:
            return None
    if not isinstance(post_time, (int, float)):
        return None
    try:
        notification_post_time = parse_alarm_time(post_time)
    except (ValueError, OverflowError, OSError):
        return None

    canonical = "\x1f".join(
        (
            REOLINK_ANDROID_PACKAGE,
            str(int(float(post_time))),
            title,
            body,
            device_name,
        )
    )
    event_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return CloudEvent(
        event_id=f"android:{event_key}",
        uid=uid,
        alarm_time=notification_post_time,
        alarm_type="MOTION",
        ai_types=(),
        source="android_notification",
        device_name=device_name,
        notification_post_time=notification_post_time,
        title=title,
        text=body,
    )


def _promote_stale_repost(event: CloudEvent, observed_at: datetime) -> CloudEvent:
    """Create one fresh event for an Android repost carrying a stale post_time.

    Some apps update/repost one existing Android notification for later alarms.
    The Companion sensor correctly emits a fresh update, but Android may expose
    the original StatusBarNotification post_time again. The recording worker
    needs the callback time in that narrow case so it searches the new clip.
    """
    occurrence_ms = round(observed_at.timestamp() * 1000)
    canonical = f"{event.event_id}\x1f{occurrence_ms}"
    occurrence_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return replace(
        event,
        event_id=f"android-repost:{occurrence_key}",
        alarm_time=observed_at,
        notification_post_time=observed_at,
    )


class NotificationBridge:
    """Listen to one HA Companion Last Notification entity without polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        expected_device_name: str,
        uid: str,
        ingest_event,
        *,
        initial_event: CloudEvent | None = None,
    ) -> None:
        self._hass = hass
        self.entity_id = entity_id
        self._expected_device_name = expected_device_name
        self._uid = uid
        self._ingest_event = ingest_event
        self._remove_listener = None
        self.last_reolink_notification_time: datetime | None = None
        self.last_reolink_notification_camera = ""
        self.last_event_matched = False
        self.last_camera_mapped = False
        self.last_event_queued = False
        self.last_duplicate_rejected = False
        # Secret-safe diagnostic-only telemetry. These fields never contain raw
        # credentials or notification media.
        self.sensor_state_change_count = 0
        self.matched_notification_update_count = 0
        self.last_sensor_state_change_time: datetime | None = None
        self.last_notification_post_time_ms: int | None = None
        self.last_post_time_changed: bool | None = None
        self.last_event_fingerprint = ""
        self.last_processing_lag_seconds: float | None = None
        self.telemetry_restored_from_pending = False
        self.stale_repost_promoted_count = 0
        self.stale_repost_suppressed_count = 0
        self.last_stale_repost_promoted = False
        self.last_effective_event_time: datetime | None = None
        self.last_effective_event_time_source = ""
        self._last_source_event_id = ""
        self._last_source_observed_at: datetime | None = None

        # The queue is persistent while these fields are runtime telemetry.
        # Restore the latest still-pending Android event on integration reload so
        # diagnostics do not misleadingly return to an all-null state.
        if initial_event is not None and initial_event.source == "android_notification":
            restored_time = initial_event.notification_post_time or initial_event.alarm_time
            self.last_reolink_notification_time = restored_time
            self.last_reolink_notification_camera = (
                initial_event.device_name or expected_device_name
            )
            self.last_event_matched = True
            self.last_camera_mapped = bool(self.last_reolink_notification_camera)
            self.last_event_queued = True
            self.last_notification_post_time_ms = round(restored_time.timestamp() * 1000)
            self.last_event_fingerprint = initial_event.event_id.removeprefix("android:")[:12]
            self.last_effective_event_time = restored_time
            self.last_effective_event_time_source = "restored_pending"
            self._last_source_event_id = initial_event.event_id
            # Restoration is telemetry only, not a post-start Android callback.
            self._last_source_observed_at = None
            self.telemetry_restored_from_pending = True

    @property
    def active(self) -> bool:
        return self._remove_listener is not None

    def start(self) -> None:
        """Bind the state listener once."""
        if self._remove_listener is not None:
            return
        self._remove_listener = async_track_state_change_event(
            self._hass, [self.entity_id], self._handle_state_change
        )

    def stop(self) -> None:
        """Unbind the state listener."""
        if self._remove_listener is None:
            return
        self._remove_listener()
        self._remove_listener = None

    @callback
    def _handle_state_change(self, event: Event) -> None:
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if new_state is None:
            return

        observed_at = datetime.now(UTC)
        new_notification = notification_event_from_attributes(
            new_state.attributes,
            expected_device_name=self._expected_device_name,
            uid=self._uid,
        )
        old_notification = (
            notification_event_from_attributes(
                old_state.attributes,
                expected_device_name=self._expected_device_name,
                uid=self._uid,
            )
            if old_state is not None
            else None
        )
        allow_stale_repost = (
            old_notification is not None
            and new_notification is not None
            and old_notification.event_id == new_notification.event_id
        )
        allow_automatic_wake = bool(
            new_notification is not None
            and is_automatic_event_fresh(new_notification, observed_at)
        )
        self._hass.async_create_task(
            self.async_process_attributes(
                new_state.attributes,
                observed_at=observed_at,
                allow_stale_repost=allow_stale_repost,
                allow_automatic_wake=allow_automatic_wake,
            ),
            "reolink_battery notification event",
        )

    async def async_process_attributes(
        self,
        attributes: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
        allow_stale_repost: bool = False,
        allow_automatic_wake: bool = True,
    ) -> bool:
        """Match, normalize and persist one notification update."""
        observed_at = observed_at or datetime.now(UTC)
        self.sensor_state_change_count += 1
        self.last_sensor_state_change_time = observed_at
        self.last_stale_repost_promoted = False

        event = notification_event_from_attributes(
            attributes,
            expected_device_name=self._expected_device_name,
            uid=self._uid,
        )
        if event is None:
            # Last Notification is a phone-wide sensor. Unrelated app updates
            # must not erase the last successfully matched Reolink telemetry.
            return False

        self.matched_notification_update_count += 1
        post_time = event.notification_post_time or event.alarm_time
        post_time_ms = round(post_time.timestamp() * 1000)
        previous_post_time_ms = self.last_notification_post_time_ms
        self.last_post_time_changed = (
            previous_post_time_ms is None or post_time_ms != previous_post_time_ms
        )
        self.last_notification_post_time_ms = post_time_ms
        self.last_event_fingerprint = event.event_id.removeprefix("android:")[:12]
        self.last_processing_lag_seconds = round(
            max(0.0, (observed_at - post_time).total_seconds()), 3
        )
        self.telemetry_restored_from_pending = False

        previous_source_id = self._last_source_event_id
        previous_source_observed_at = self._last_source_observed_at
        same_source_recent = (
            previous_source_id == event.event_id
            and previous_source_observed_at is not None
            and (observed_at - previous_source_observed_at).total_seconds()
            < _STALE_REPOST_DEBOUNCE_SECONDS
        )

        self.last_event_matched = True
        self.last_camera_mapped = True
        self.last_reolink_notification_time = event.notification_post_time
        self.last_reolink_notification_camera = event.device_name
        self.last_effective_event_time = post_time
        self.last_effective_event_time_source = "notification_post_time"

        source_event = event
        stale_repost_candidate = bool(
            allow_stale_repost
            and self.last_processing_lag_seconds is not None
            and self.last_processing_lag_seconds >= _STALE_REPOST_MIN_AGE_SECONDS
        )
        source_can_wake = (
            allow_automatic_wake
            and not stale_repost_candidate
            and is_automatic_event_fresh(source_event, observed_at)
        )
        added = await self._ingest_event(source_event, source_can_wake)

        # The Companion Last Notification sensor is INTENT_ONLY and force-updates
        # on Android onNotificationPosted callbacks. If the same source event ID
        # comes back with a post_time older than five minutes, a proven post-start
        # update of that source may represent a new occurrence. The stale source
        # itself is retained without wake permission; only this callback-time
        # promotion may wake the automatic worker.
        # Collapse rapid reposts to avoid duplicate downloads caused by one
        # Android notification being updated several times.
        if stale_repost_candidate:
            if same_source_recent:
                self.stale_repost_suppressed_count += 1
            else:
                promoted = _promote_stale_repost(source_event, observed_at)
                promoted_added = await self._ingest_event(promoted, True)
                if promoted_added:
                    event = promoted
                    added = promoted_added
                    self.stale_repost_promoted_count += 1
                    self.last_stale_repost_promoted = True
                    self.last_effective_event_time = observed_at
                    self.last_effective_event_time_source = "sensor_callback_repost"
                    self.last_reolink_notification_time = observed_at

        self._last_source_event_id = source_event.event_id
        self._last_source_observed_at = observed_at
        self.last_event_queued = bool(added)
        self.last_duplicate_rejected = not bool(added)
        return bool(added)
