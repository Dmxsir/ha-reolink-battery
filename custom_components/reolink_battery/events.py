"""Cloud-event parsing and persistent deduplication primitives."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .const import (
    AUTOMATIC_RECORDING_EVENT_MAX_AGE,
    MAX_COMPLETED_RECORDINGS,
    MAX_PENDING_EVENTS,
    MAX_PROCESSED_EVENT_IDS,
    MEANINGFUL_ALARM_TYPES,
)


@dataclass(frozen=True, slots=True)
class CloudEvent:
    """The non-secret event fields needed by the future recording worker."""

    event_id: str
    uid: str
    alarm_time: datetime
    alarm_type: str
    ai_types: tuple[str, ...] = ()
    source: str = "message_center"
    device_name: str = ""
    notification_post_time: datetime | None = None
    title: str = ""
    text: str = ""

    def as_storage(self) -> dict[str, object]:
        data = asdict(self)
        data["alarm_time"] = self.alarm_time.isoformat()
        data["ai_types"] = list(self.ai_types)
        if self.notification_post_time is not None:
            data["notification_post_time"] = self.notification_post_time.isoformat()
        return data

    @classmethod
    def from_storage(cls, data: dict[str, object]) -> CloudEvent:
        notification_post_time = data.get("notification_post_time")
        return cls(
            event_id=str(data["event_id"]),
            uid=str(data["uid"]),
            alarm_time=parse_alarm_time(data["alarm_time"]),
            alarm_type=str(data.get("alarm_type") or ""),
            ai_types=tuple(str(value) for value in data.get("ai_types", [])),
            source=str(data.get("source") or "message_center"),
            device_name=str(data.get("device_name") or ""),
            notification_post_time=(
                parse_alarm_time(notification_post_time)
                if notification_post_time is not None
                else None
            ),
            title=str(data.get("title") or ""),
            text=str(data.get("text") or ""),
        )


@dataclass(frozen=True, slots=True)
class CompletedRecording:
    """Persistent secret-safe identity for one verified SD recording."""

    fingerprint: str
    start_time: datetime
    end_time: datetime
    size: int

    def as_storage(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "size": self.size,
        }

    @classmethod
    def from_storage(cls, data: dict[str, object]) -> CompletedRecording:
        return cls(
            fingerprint=str(data["fingerprint"]),
            start_time=datetime.fromisoformat(str(data["start_time"])),
            end_time=datetime.fromisoformat(str(data["end_time"])),
            size=int(data.get("size") or 0),
        )


@dataclass(frozen=True, slots=True)
class DeferredEvent:
    """Persistent reason that automatic processing must skip one event."""

    event_id: str
    reason: str
    deferred_at: datetime

    def as_storage(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "reason": self.reason,
            "deferred_at": self.deferred_at.isoformat(),
        }

    @classmethod
    def from_storage(cls, data: dict[str, object]) -> DeferredEvent:
        return cls(
            event_id=str(data["event_id"]),
            reason=str(data.get("reason") or "unspecified"),
            deferred_at=parse_alarm_time(data["deferred_at"]),
        )


def parse_alarm_time(value: object) -> datetime:
    """Normalize ISO or epoch-second/millisecond values to aware UTC."""
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if value > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, UTC)
    if not isinstance(value, str) or not value:
        raise ValueError("event alarmAt is missing")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def is_automatic_event_fresh(event: CloudEvent, now: datetime) -> bool:
    """Return whether an event may automatically wake the camera."""
    event_time = event.notification_post_time or event.alarm_time
    age = now.astimezone(UTC) - event_time.astimezone(UTC)
    return age <= AUTOMATIC_RECORDING_EVENT_MAX_AGE


def _items(payload: object) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        return (item for item in payload if isinstance(item, dict))
    if not isinstance(payload, dict):
        return ()
    for key in ("items", "messages", "msgs", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return (item for item in value if isinstance(item, dict))
    data = payload.get("data")
    return _items(data) if isinstance(data, (dict, list)) else ()


def _ai_types(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        values = [str(part).strip() for part in value]
    else:
        values = []
    return tuple(dict.fromkeys(value for value in values if value))


def parse_cloud_events(payload: object, expected_uid: str) -> list[CloudEvent]:
    """Parse the observed decoded Message Center structure."""
    events: list[CloudEvent] = []
    for message in _items(payload):
        event_id = message.get("id")
        uid = message.get("uid")
        uid_matches = uid in {expected_uid, f"{expected_uid}_00"}
        alarm_at = message.get("alarmAt")
        raw = message.get("raw")
        alarm = raw.get("alarm") if isinstance(raw, dict) else None
        alarm_type = ""
        ai = ()
        if isinstance(alarm, dict):
            alarm_type = str(alarm.get("type") or "").upper()
            ai = _ai_types(alarm.get("aiType"))
        if not alarm_type:
            alarm_type = str(message.get("alarmType") or "").upper()
        if not ai:
            ai = _ai_types(message.get("aiType"))
        if (
            not isinstance(event_id, (str, int))
            or not isinstance(uid, str)
            or not uid_matches
            or alarm_at is None
            or (alarm_type not in MEANINGFUL_ALARM_TYPES and not ai)
        ):
            continue
        try:
            alarm_time = parse_alarm_time(alarm_at)
        except (ValueError, OverflowError, OSError):
            continue
        events.append(
            CloudEvent(str(event_id), expected_uid, alarm_time, alarm_type, ai)
        )
    return events


class EventQueue:
    """A bounded, serializable queue with event-id deduplication."""

    def __init__(self) -> None:
        self._processed: list[str] = []
        self._processed_set: set[str] = set()
        self._pending: list[CloudEvent] = []
        self._completed_recordings: list[CompletedRecording] = []
        self._deferred: dict[str, DeferredEvent] = {}

    @property
    def pending(self) -> tuple[CloudEvent, ...]:
        return tuple(self._pending)

    @property
    def processed_count(self) -> int:
        return len(self._processed)

    @property
    def completed_recording_fingerprints(self) -> frozenset[str]:
        return frozenset(item.fingerprint for item in self._completed_recordings)

    @property
    def completed_recording_count(self) -> int:
        return len(self._completed_recordings)

    @property
    def deferred_event_ids(self) -> frozenset[str]:
        return frozenset(self._deferred)

    @property
    def deferred_count(self) -> int:
        return len(self._deferred)

    @property
    def last_deferred(self) -> DeferredEvent | None:
        return max(
            self._deferred.values(),
            key=lambda item: item.deferred_at,
            default=None,
        )

    def remember_completed_recording(self, recording: CompletedRecording) -> bool:
        if recording.fingerprint in self.completed_recording_fingerprints:
            return False
        self._completed_recordings.append(recording)
        if len(self._completed_recordings) > MAX_COMPLETED_RECORDINGS:
            self._completed_recordings = self._completed_recordings[-MAX_COMPLETED_RECORDINGS:]
        return True

    def enqueue(self, event: CloudEvent) -> bool:
        if event.event_id in self._processed_set:
            return False
        self._processed.append(event.event_id)
        self._processed_set.add(event.event_id)
        self._pending.append(event)
        if len(self._processed) > MAX_PROCESSED_EVENT_IDS:
            expired = self._processed.pop(0)
            if all(item.event_id != expired for item in self._pending):
                self._processed_set.discard(expired)
        if len(self._pending) > MAX_PENDING_EVENTS:
            expired_pending = self._pending.pop(0)
            self._deferred.pop(expired_pending.event_id, None)
        return True

    def remove(self, event_id: str) -> bool:
        before = len(self._pending)
        self._pending = [event for event in self._pending if event.event_id != event_id]
        deferred_removed = self._deferred.pop(event_id, None) is not None
        return len(self._pending) != before or deferred_removed

    def defer(self, event_id: str, reason: str, deferred_at: datetime) -> bool:
        if not any(event.event_id == event_id for event in self._pending):
            return False
        deferred = DeferredEvent(event_id, reason, deferred_at.astimezone(UTC))
        if self._deferred.get(event_id) == deferred:
            return False
        self._deferred[event_id] = deferred
        return True

    def rearm(self, event_id: str) -> bool:
        return self._deferred.pop(event_id, None) is not None

    def discard_deferred(self, event_ids: Iterable[str]) -> tuple[str, ...]:
        """Remove only selected deferred pending events and keep dedupe history."""
        requested = set(event_ids)
        if not requested:
            return ()
        removed = tuple(
            event.event_id
            for event in self._pending
            if event.event_id in requested and event.event_id in self._deferred
        )
        if not removed:
            return ()
        removed_set = set(removed)
        self._pending = [
            event for event in self._pending if event.event_id not in removed_set
        ]
        for event_id in removed:
            self._deferred.pop(event_id, None)
        return removed

    def load(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        processed = data.get("processed")
        pending = data.get("pending")
        completed_recordings = data.get("completed_recordings")
        deferred_events = data.get("deferred_events")
        self._deferred = {}
        if isinstance(processed, list):
            self._processed = [str(value) for value in processed][
                -MAX_PROCESSED_EVENT_IDS:
            ]
            self._processed_set = set(self._processed)
        if isinstance(pending, list):
            restored: list[CloudEvent] = []
            for item in pending[-MAX_PENDING_EVENTS:]:
                if not isinstance(item, dict):
                    continue
                try:
                    restored.append(CloudEvent.from_storage(item))
                except (KeyError, TypeError, ValueError):
                    continue
            self._pending = restored
            for event in restored:
                if event.event_id not in self._processed_set:
                    self._processed.append(event.event_id)
                    self._processed_set.add(event.event_id)
        if isinstance(completed_recordings, list):
            restored_recordings: list[CompletedRecording] = []
            for item in completed_recordings[-MAX_COMPLETED_RECORDINGS:]:
                if not isinstance(item, dict):
                    continue
                try:
                    restored_recordings.append(CompletedRecording.from_storage(item))
                except (KeyError, TypeError, ValueError):
                    continue
            self._completed_recordings = restored_recordings
        if isinstance(deferred_events, list):
            pending_ids = {event.event_id for event in self._pending}
            restored_deferred: dict[str, DeferredEvent] = {}
            for item in deferred_events[-MAX_PENDING_EVENTS:]:
                if not isinstance(item, dict):
                    continue
                try:
                    deferred = DeferredEvent.from_storage(item)
                except (KeyError, TypeError, ValueError, OverflowError, OSError):
                    continue
                if deferred.event_id in pending_ids:
                    restored_deferred[deferred.event_id] = deferred
            self._deferred = restored_deferred

    def as_storage(self) -> dict[str, object]:
        return {
            "processed": self._processed[-MAX_PROCESSED_EVENT_IDS:],
            "pending": [event.as_storage() for event in self._pending],
            "completed_recordings": [
                recording.as_storage()
                for recording in self._completed_recordings[-MAX_COMPLETED_RECORDINGS:]
            ],
            "deferred_events": [
                deferred.as_storage() for deferred in self._deferred.values()
            ],
        }
