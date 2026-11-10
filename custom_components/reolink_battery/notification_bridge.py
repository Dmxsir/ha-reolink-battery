"""Home Assistant Companion notification bridge for legacy Reolink cameras."""

from __future__ import annotations

import hashlib
import re
from collections import deque
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
_CALLBACK_DECISION_HISTORY_LIMIT = 8
_NOTIFICATION_IDENTITY_HISTORY_LIMIT = 16
_SEEN_POST_TIME_HISTORY_LIMIT = 64
_ACTIVE_NOTIFICATION_POST_TIME_KEY = re.compile(
    rf"^{re.escape(REOLINK_ANDROID_PACKAGE)}_(-?\d+)_post_time$"
)
_RELEVANT_ATTRIBUTE_KEYS = (
    "package",
    "android.title",
    "android.text",
    "android.bigText",
    "channel_id",
    "post_time",
)
_MISSING = object()


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


def _safe_post_time_ms(attributes: Mapping[str, Any]) -> int | None:
    """Return a normalized timestamp without exposing the raw attribute."""
    post_time = attributes.get("post_time")
    if isinstance(post_time, str):
        try:
            post_time = int(post_time)
        except ValueError:
            return None
    if not isinstance(post_time, (int, float)):
        return None
    try:
        parsed = parse_alarm_time(post_time)
    except (ValueError, OverflowError, OSError):
        return None
    return round(parsed.timestamp() * 1000)


def _metadata_text(value: object) -> str:
    """Return meaningful metadata text while treating Companion nulls as absent."""
    text = _text(value)
    return "" if text.casefold() in {"null", "none"} else text


def _metadata_hash(value: object) -> str:
    """Hash one metadata value without exporting it."""
    text = _metadata_text(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else ""


def _empty_notification_identity_observation(
    observed_at: datetime, *, package_is_reolink: bool, reason: str
) -> dict[str, Any]:
    """Build one fixed-shape, secret-safe identity observation."""
    return {
        "observation_time": observed_at.isoformat(),
        "source": "companion_active_notification_count",
        "reason": reason,
        "package_is_reolink": package_is_reolink,
        "notification_id_present": False,
        "notification_id": None,
        "notification_identity_hash": "",
        "notification_id_source": "not_available",
        "notification_key_present": False,
        "notification_key_hash": "",
        "tag_present": False,
        "tag_hash": "",
        "group_key_present": False,
        "group_key_hash": "",
        "group_present": False,
        "group_hash": "",
        "post_time_present": False,
        "post_time_type_valid": False,
        "post_time_parse_valid": False,
        "post_time_ms": None,
        "channel_present": False,
        "channel_hash": "",
        "channel_is_reolink_notification_channel": False,
        "category_present": False,
        "category_hash": "",
        "title_present": False,
        "title_hash": "",
        "text_present": False,
        "big_text_present": False,
        "camera_extracted": False,
        "camera_match": False,
        "normalized": False,
        "last_notification_relation": "not_available",
    }


def _active_reolink_notification_records(
    attributes: Mapping[str, Any],
) -> list[tuple[int, dict[str, Any]]]:
    """Rebuild each active Reolink notification in Last Notification shape.

    Active Notification Count flattens every active notification into
    ``<package>_<id>_<field>`` and ``<extra>_<package>_<id>`` attributes. Only
    keys of the Reolink package are read; other apps' entries are never copied.
    """
    record_ids: dict[str, int] = {}
    for key in attributes:
        if not isinstance(key, str):
            continue
        match = _ACTIVE_NOTIFICATION_POST_TIME_KEY.fullmatch(key)
        if match is not None:
            record_ids[match.group(1)] = int(match.group(1))

    records: list[tuple[int, dict[str, Any]]] = []
    for raw_id, notification_id in sorted(
        record_ids.items(), key=lambda item: (item[1], item[0])
    ):
        extra_suffix = f"_{REOLINK_ANDROID_PACKAGE}_{raw_id}"
        metadata_prefix = f"{REOLINK_ANDROID_PACKAGE}_{raw_id}_"
        records.append(
            (
                notification_id,
                {
                    "package": REOLINK_ANDROID_PACKAGE,
                    "android.title": attributes.get(f"android.title{extra_suffix}"),
                    "android.text": attributes.get(f"android.text{extra_suffix}"),
                    "android.bigText": attributes.get(
                        f"android.bigText{extra_suffix}"
                    ),
                    "channel_id": attributes.get(f"{metadata_prefix}channel_id"),
                    "group_id": attributes.get(f"{metadata_prefix}group_id"),
                    "category": attributes.get(f"{metadata_prefix}category"),
                    "post_time": attributes.get(f"{metadata_prefix}post_time"),
                },
            )
        )
    return records


def _active_notification_identity_observations(
    attributes: Mapping[str, Any],
    *,
    expected_device_name: str,
    observed_at: datetime,
    last_callback_post_time_ms: int | None,
) -> list[dict[str, Any]]:
    """Extract only Reolink identity metadata encoded by Active Notification Count."""
    records = _active_reolink_notification_records(attributes)

    if not records:
        reolink_key_present = any(
            isinstance(key, str) and REOLINK_ANDROID_PACKAGE in key
            for key in attributes
        )
        return [
            _empty_notification_identity_observation(
                observed_at,
                package_is_reolink=reolink_key_present,
                reason=(
                    "reolink_record_missing_numeric_id"
                    if reolink_key_present
                    else "no_reolink_record"
                ),
            )
        ]

    observations: list[dict[str, Any]] = []
    for notification_id, record_attributes in records:
        _event, decision = _notification_event_from_attributes_with_decision(
            record_attributes,
            expected_device_name=expected_device_name,
            uid="diagnostic-only",
        )
        post_time_ms = _safe_post_time_ms(record_attributes)
        group = _metadata_text(record_attributes["group_id"])
        channel = _metadata_text(record_attributes["channel_id"])
        category = _metadata_text(record_attributes["category"])
        title = _metadata_text(record_attributes["android.title"])
        text = _metadata_text(record_attributes["android.text"])
        big_text = _metadata_text(record_attributes["android.bigText"])
        relation = "not_available"
        if last_callback_post_time_ms is not None and post_time_ms is not None:
            relation = (
                "exact_post_time_match"
                if post_time_ms == last_callback_post_time_ms
                else "different_post_time"
            )
        observation = _empty_notification_identity_observation(
            observed_at,
            package_is_reolink=True,
            reason="active_reolink_record",
        )
        observation.update(
            {
                "notification_id_present": True,
                "notification_id": notification_id,
                "notification_identity_hash": hashlib.sha256(
                    f"{REOLINK_ANDROID_PACKAGE}\x1f{notification_id}".encode()
                ).hexdigest()[:12],
                "notification_id_source": "active_attribute_suffix",
                # Companion exports Notification.group, not StatusBarNotification.groupKey.
                "group_present": bool(group),
                "group_hash": _metadata_hash(group),
                "post_time_present": decision["post_time_present"],
                "post_time_type_valid": decision["post_time_type_valid"],
                "post_time_parse_valid": decision["post_time_parse_valid"],
                "post_time_ms": post_time_ms,
                "channel_present": bool(channel),
                "channel_hash": _metadata_hash(channel),
                "channel_is_reolink_notification_channel": (
                    channel == REOLINK_NOTIFICATION_CHANNEL
                ),
                "category_present": bool(category),
                "category_hash": _metadata_hash(category),
                "title_present": bool(title),
                "title_hash": _metadata_hash(title),
                "text_present": bool(text),
                "big_text_present": bool(big_text),
                "camera_extracted": decision["camera_extracted"],
                "camera_match": decision["camera_match"],
                "normalized": decision["normalized"],
                "last_notification_relation": relation,
            }
        )
        observations.append(observation)
    return observations


def _relevant_attributes_hash(attributes: Mapping[str, Any]) -> str:
    """Hash only attributes consumed by the bridge."""
    canonical = "\x1f".join(
        "\x1e".join(
            (
                key,
                "present" if key in attributes else "missing",
                type(attributes.get(key)).__name__,
                _text(attributes.get(key)),
            )
        )
        for key in _RELEVANT_ATTRIBUTE_KEYS
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _changed_relevant_fields(
    old_attributes: Mapping[str, Any], new_attributes: Mapping[str, Any]
) -> list[str]:
    return [
        key
        for key in _RELEVANT_ATTRIBUTE_KEYS
        if old_attributes.get(key, _MISSING) != new_attributes.get(key, _MISSING)
    ]


def _callback_decision(
    old_state: State | None, new_state: State | None, observed_at: datetime
) -> dict[str, Any]:
    """Build one bounded, secret-safe snapshot for the current HA callback."""
    old_attributes = old_state.attributes if old_state is not None else {}
    new_attributes = new_state.attributes if new_state is not None else {}
    if old_state is None:
        classification = "created"
    elif new_state is None:
        classification = "unknown"
    else:
        state_changed = getattr(old_state, "state", None) != getattr(
            new_state, "state", None
        )
        attributes_changed = old_attributes != new_attributes
        if state_changed and attributes_changed:
            classification = "state_and_attributes"
        elif state_changed:
            classification = "state_only"
        elif attributes_changed:
            classification = "attributes_only"
        else:
            classification = "force_update_unchanged"

    return {
        "scope": "latest_home_assistant_sensor_callback",
        "callback_time": observed_at.isoformat(),
        "change_classification": classification,
        "old_state_existed": old_state is not None,
        "old_relevant_attributes_hash": (
            _relevant_attributes_hash(old_attributes) if old_state is not None else ""
        ),
        "new_relevant_attributes_hash": (
            _relevant_attributes_hash(new_attributes) if new_state is not None else ""
        ),
        "changed_relevant_fields": (
            _changed_relevant_fields(old_attributes, new_attributes)
            if old_state is not None and new_state is not None
            else [key for key in _RELEVANT_ATTRIBUTE_KEYS if key in new_attributes]
            if new_state is not None
            else []
        ),
        "old_post_time_ms": (
            _safe_post_time_ms(old_attributes) if old_state is not None else None
        ),
        "new_post_time_ms": (
            _safe_post_time_ms(new_attributes) if new_state is not None else None
        ),
        "normalized": False,
        "rejection_reason": "new_state_missing" if new_state is None else None,
        "package_is_reolink": False,
        "notification_text_present": False,
        "big_text_present": False,
        "camera_extracted": False,
        "camera_match": False,
        "fallback_path_used": False,
        "post_time_present": False,
        "post_time_type_valid": False,
        "post_time_parse_valid": False,
        "candidate_fingerprint": "",
        "post_time_changed": None,
        "stale_repost_evaluation_reached": False,
        "stale_repost_decision": "not_reached",
        "source_wake_permission": None,
        "promoted_wake_permission": None,
        "source_duplicate_rejected": None,
        "duplicate_rejected": None,
        "source_queue_result": "not_reached",
        "queue_result": "not_reached",
    }


def _notification_event_from_attributes_with_decision(
    attributes: Mapping[str, Any], *, expected_device_name: str, uid: str
) -> tuple[CloudEvent | None, dict[str, Any]]:
    """Normalize an Android notification and report the exact parser decision."""
    package = _text(attributes.get("package"))
    title = _text(attributes.get("android.title"))
    text = _text(attributes.get("android.text"))
    big_text = _text(attributes.get("android.bigText"))
    body = big_text or text
    post_time = attributes.get("post_time")
    parsed_post_time_ms = _safe_post_time_ms(attributes)
    decision: dict[str, Any] = {
        "normalized": False,
        "rejection_reason": None,
        "package_is_reolink": package == REOLINK_ANDROID_PACKAGE,
        "notification_text_present": bool(body),
        "big_text_present": bool(big_text),
        "camera_extracted": False,
        "camera_match": False,
        "fallback_path_used": False,
        "post_time_present": "post_time" in attributes and post_time is not None,
        "post_time_type_valid": isinstance(post_time, (str, int, float)),
        "post_time_parse_valid": parsed_post_time_ms is not None,
        "candidate_fingerprint": "",
    }

    if not package:
        decision["rejection_reason"] = "package_missing"
        return None, decision
    if package != REOLINK_ANDROID_PACKAGE:
        decision["rejection_reason"] = "package_not_reolink"
        return None, decision
    if not body:
        decision["rejection_reason"] = "notification_text_missing"
        return None, decision

    extracted_name = _extract_camera_name(body)
    expected = _normalized(expected_device_name)
    if extracted_name is not None:
        decision["camera_extracted"] = True
        decision["camera_match"] = _normalized(extracted_name) == expected
        if not decision["camera_match"]:
            decision["rejection_reason"] = "camera_mismatch"
            return None, decision
        device_name = expected_device_name
    else:
        decision["fallback_path_used"] = True
        channel = _text(attributes.get("channel_id"))
        looks_like_alarm = (
            _normalized(title) == "camera alert"
            or channel == REOLINK_NOTIFICATION_CHANNEL
        )
        if not looks_like_alarm:
            decision["rejection_reason"] = "fallback_alarm_signature_failed"
            return None, decision
        decision["camera_match"] = expected in _normalized(body)
        if not decision["camera_match"]:
            decision["rejection_reason"] = "configured_camera_absent_from_fallback"
            return None, decision
        device_name = expected_device_name

    if "post_time" not in attributes or post_time is None:
        decision["rejection_reason"] = "post_time_missing"
        return None, decision
    if isinstance(post_time, str):
        try:
            post_time = int(post_time)
        except ValueError:
            decision["rejection_reason"] = "post_time_parse_failed"
            return None, decision
    if not isinstance(post_time, (int, float)):
        decision["rejection_reason"] = "post_time_wrong_type"
        return None, decision
    try:
        notification_post_time = parse_alarm_time(post_time)
    except (ValueError, OverflowError, OSError):
        decision["rejection_reason"] = "post_time_parse_failed"
        return None, decision

    decision["post_time_parse_valid"] = True
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
    decision["normalized"] = True
    decision["rejection_reason"] = "normalized"
    decision["candidate_fingerprint"] = event_key[:12]
    return (
        CloudEvent(
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
        ),
        decision,
    )


def notification_event_from_attributes(
    attributes: Mapping[str, Any], *, expected_device_name: str, uid: str
) -> CloudEvent | None:
    """Normalize one public Android notification into a queued camera event."""
    event, _decision = _notification_event_from_attributes_with_decision(
        attributes,
        expected_device_name=expected_device_name,
        uid=uid,
    )
    return event


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
        identity_entity_id: str | None = None,
    ) -> None:
        self._hass = hass
        self.entity_id = entity_id
        self._expected_device_name = expected_device_name
        self._uid = uid
        self._ingest_event = ingest_event
        self._remove_listener = None
        self.identity_entity_id = identity_entity_id
        self._remove_identity_listener = None
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
        self.last_callback_decision: dict[str, Any] = {}
        self.recent_callback_decisions: deque[dict[str, Any]] = deque(
            maxlen=_CALLBACK_DECISION_HISTORY_LIMIT
        )
        self.identity_sensor_state_change_count = 0
        self.last_identity_sensor_state_change_time: datetime | None = None
        self.recent_notification_identity_observations: deque[dict[str, Any]] = deque(
            maxlen=_NOTIFICATION_IDENTITY_HISTORY_LIMIT
        )
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
        # Alarm post_times either sensor already handed over in this runtime.
        # It only stops one alarm being offered on every identity update; the
        # persistent queue remains the authority on duplicates.
        self._seen_post_time_ms: deque[int] = deque(
            maxlen=_SEEN_POST_TIME_HISTORY_LIMIT
        )
        self.identity_candidate_count = 0
        self.identity_recovered_event_count = 0
        self.identity_duplicate_count = 0
        self.last_identity_recovered_event_time: datetime | None = None
        self.last_identity_recovered_wake_permission: bool | None = None
        self.textless_reolink_notification_count = 0
        self._last_textless_post_time_ms: int | None = None

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

    @property
    def identity_listener_active(self) -> bool:
        return self._remove_identity_listener is not None

    def _record_callback_decision(self, decision: dict[str, Any]) -> None:
        """Record one callback, ordered oldest to newest and strictly bounded."""
        self.last_callback_decision = decision
        self.recent_callback_decisions.append(decision)

    def _mark_matching_identity_observations(self, post_time_ms: int | None) -> None:
        """Mark exact post-time equality; never infer identity from proximity."""
        if post_time_ms is None:
            return
        for observation in self.recent_notification_identity_observations:
            if observation["post_time_ms"] == post_time_ms:
                observation["last_notification_relation"] = "exact_post_time_match"

    def start(self) -> None:
        """Bind the state listener once."""
        if self._remove_listener is None:
            self._remove_listener = async_track_state_change_event(
                self._hass, [self.entity_id], self._handle_state_change
            )
        if self.identity_entity_id and self._remove_identity_listener is None:
            self._remove_identity_listener = async_track_state_change_event(
                self._hass,
                [self.identity_entity_id],
                self._handle_identity_state_change,
            )

    def stop(self) -> None:
        """Unbind the state listener."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        if self._remove_identity_listener is not None:
            self._remove_identity_listener()
            self._remove_identity_listener = None

    def _mark_post_time_seen(self, post_time_ms: int) -> bool:
        """Return True the first time this runtime meets one alarm post_time."""
        if post_time_ms in self._seen_post_time_ms:
            return False
        self._seen_post_time_ms.append(post_time_ms)
        return True

    def _count_textless_reolink_notification(
        self, decision: Mapping[str, Any]
    ) -> None:
        """Count Reolink updates that reached Last Notification without text.

        Each distinct post_time is one notification whose alarm text this
        sensor did not carry. identity_recovered_event_count shows how many
        alarms Active Notification Count supplied instead.
        """
        post_time_ms = decision.get("new_post_time_ms")
        if (
            decision.get("rejection_reason") != "notification_text_missing"
            or not decision.get("package_is_reolink")
            or post_time_ms is None
            or post_time_ms == self._last_textless_post_time_ms
        ):
            return
        self._last_textless_post_time_ms = post_time_ms
        self.textless_reolink_notification_count += 1

    def _offer_identity_events(
        self, attributes: Mapping[str, Any], observed_at: datetime
    ) -> None:
        """Queue alarms that Last Notification never delivered with their text.

        Android can post a text-less group summary right after a Reolink alarm,
        and the Companion Last Notification sensor then reports only that
        summary. The alarm itself is still listed by Active Notification Count,
        so every listed alarm is offered once. Both sensors derive the same
        event id for one notification, so the queue rejects an alarm that Last
        Notification already delivered.
        """
        for _notification_id, record in _active_reolink_notification_records(
            attributes
        ):
            event = notification_event_from_attributes(
                record,
                expected_device_name=self._expected_device_name,
                uid=self._uid,
            )
            if event is None:
                continue
            post_time = event.notification_post_time or event.alarm_time
            if not self._mark_post_time_seen(round(post_time.timestamp() * 1000)):
                continue
            self.identity_candidate_count += 1
            self._hass.async_create_task(
                self._async_ingest_identity_event(event, observed_at),
                "reolink_battery identity notification event",
            )

    async def _async_ingest_identity_event(
        self, event: CloudEvent, observed_at: datetime
    ) -> bool:
        """Persist one alarm seen only through Active Notification Count.

        A stale alarm is still queued, without permission to wake the camera,
        exactly as a stale Last Notification update is.
        """
        allow_automatic_wake = is_automatic_event_fresh(event, observed_at)
        added = await self._ingest_event(event, allow_automatic_wake)
        if not added:
            self.identity_duplicate_count += 1
            return False

        post_time = event.notification_post_time or event.alarm_time
        self.identity_recovered_event_count += 1
        self.last_identity_recovered_event_time = post_time
        self.last_identity_recovered_wake_permission = allow_automatic_wake
        self.last_event_matched = True
        self.last_camera_mapped = True
        self.last_event_queued = True
        self.last_duplicate_rejected = False
        self.last_reolink_notification_time = post_time
        self.last_reolink_notification_camera = event.device_name
        self.last_event_fingerprint = event.event_id.removeprefix("android:")[:12]
        self.last_effective_event_time = post_time
        self.last_effective_event_time_source = "active_notification_count"
        self.telemetry_restored_from_pending = False
        return True

    @callback
    def _handle_identity_state_change(self, event: Event) -> None:
        """Record Active Notification Count metadata and recover hidden alarms."""
        new_state: State | None = event.data.get("new_state")
        observed_at = datetime.now(UTC)
        self.identity_sensor_state_change_count += 1
        self.last_identity_sensor_state_change_time = observed_at
        if new_state is None:
            observations = [
                _empty_notification_identity_observation(
                    observed_at,
                    package_is_reolink=False,
                    reason="identity_state_missing",
                )
            ]
        else:
            last_callback_post_time_ms = (
                self.last_callback_decision.get("new_post_time_ms")
                if self.last_callback_decision.get("package_is_reolink")
                else None
            )
            observations = _active_notification_identity_observations(
                new_state.attributes,
                expected_device_name=self._expected_device_name,
                observed_at=observed_at,
                last_callback_post_time_ms=last_callback_post_time_ms,
            )
        self.recent_notification_identity_observations.extend(observations)
        if new_state is not None:
            self._offer_identity_events(new_state.attributes, observed_at)

    @callback
    def _handle_state_change(self, event: Event) -> None:
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        observed_at = datetime.now(UTC)
        decision_snapshot = _callback_decision(old_state, new_state, observed_at)
        self._record_callback_decision(decision_snapshot)
        if new_state is None:
            return

        new_notification, parser_decision = (
            _notification_event_from_attributes_with_decision(
                new_state.attributes,
                expected_device_name=self._expected_device_name,
                uid=self._uid,
            )
        )
        decision_snapshot.update(parser_decision)
        if decision_snapshot["package_is_reolink"]:
            self._mark_matching_identity_observations(
                decision_snapshot["new_post_time_ms"]
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
                decision_snapshot=decision_snapshot,
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
        decision_snapshot: dict[str, Any] | None = None,
    ) -> bool:
        """Match, normalize and persist one notification update."""
        observed_at = observed_at or datetime.now(UTC)
        if decision_snapshot is None:
            decision_snapshot = _callback_decision(None, None, observed_at)
            decision_snapshot.update(
                {
                    "change_classification": "unknown",
                    "new_relevant_attributes_hash": _relevant_attributes_hash(
                        attributes
                    ),
                    "changed_relevant_fields": [
                        key for key in _RELEVANT_ATTRIBUTE_KEYS if key in attributes
                    ],
                    "new_post_time_ms": _safe_post_time_ms(attributes),
                    "rejection_reason": None,
                }
            )
            self._record_callback_decision(decision_snapshot)
        self.sensor_state_change_count += 1
        self.last_sensor_state_change_time = observed_at
        self.last_stale_repost_promoted = False

        event, parser_decision = _notification_event_from_attributes_with_decision(
            attributes,
            expected_device_name=self._expected_device_name,
            uid=self._uid,
        )
        decision_snapshot.update(parser_decision)
        if event is None:
            # Last Notification is a phone-wide sensor. Unrelated app updates
            # must not erase the last successfully matched Reolink telemetry.
            self._count_textless_reolink_notification(decision_snapshot)
            return False

        self.matched_notification_update_count += 1
        post_time = event.notification_post_time or event.alarm_time
        post_time_ms = round(post_time.timestamp() * 1000)
        self._mark_post_time_seen(post_time_ms)
        previous_post_time_ms = self.last_notification_post_time_ms
        self.last_post_time_changed = (
            previous_post_time_ms is None or post_time_ms != previous_post_time_ms
        )
        decision_snapshot["post_time_changed"] = self.last_post_time_changed
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
        decision_snapshot["stale_repost_evaluation_reached"] = True
        if not allow_stale_repost:
            decision_snapshot["stale_repost_decision"] = (
                "not_candidate_no_matching_runtime_source"
            )
        elif not stale_repost_candidate:
            decision_snapshot["stale_repost_decision"] = (
                "not_candidate_source_fresh"
            )
        else:
            decision_snapshot["stale_repost_decision"] = "candidate"
        decision_snapshot["source_wake_permission"] = bool(source_can_wake)
        added = await self._ingest_event(source_event, source_can_wake)
        decision_snapshot["source_duplicate_rejected"] = not bool(added)
        decision_snapshot["source_queue_result"] = (
            "queued" if added else "duplicate_rejected"
        )

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
                decision_snapshot["stale_repost_decision"] = (
                    "suppressed_recent_source"
                )
            else:
                promoted = _promote_stale_repost(source_event, observed_at)
                decision_snapshot["promoted_wake_permission"] = True
                promoted_added = await self._ingest_event(promoted, True)
                if promoted_added:
                    event = promoted
                    added = promoted_added
                    self.stale_repost_promoted_count += 1
                    self.last_stale_repost_promoted = True
                    self.last_effective_event_time = observed_at
                    self.last_effective_event_time_source = "sensor_callback_repost"
                    self.last_reolink_notification_time = observed_at
                    decision_snapshot["stale_repost_decision"] = "promoted_queued"
                else:
                    decision_snapshot["stale_repost_decision"] = (
                        "promoted_duplicate_rejected"
                    )

        self._last_source_event_id = source_event.event_id
        self._last_source_observed_at = observed_at
        self.last_event_queued = bool(added)
        self.last_duplicate_rejected = not bool(added)
        decision_snapshot["duplicate_rejected"] = not bool(added)
        decision_snapshot["queue_result"] = (
            "queued" if added else "duplicate_rejected"
        )
        return bool(added)
