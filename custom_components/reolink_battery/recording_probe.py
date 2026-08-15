"""Battery-safe manual recording lookup for Milestone 3B.2a."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from reolink_aio.api import Host
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError
from reolink_aio.typings import VOD_file

from .camera import CameraStageError, prepare_standalone_channel_zero
from .events import CloudEvent
from .transport import (
    BoundBaichuanUdpConnection,
    linux_ipv4_interface,
    resolve_uid_lan,
    validate_local_lan_route,
)

_LOGGER = logging.getLogger(__name__)
DEFAULT_MATCH_TOLERANCE = timedelta(seconds=120)


@dataclass(frozen=True, slots=True)
class RecordingCandidate:
    """Secret-safe metadata for the best SD recording near one notification."""

    file_name: str
    start_time: datetime
    end_time: datetime
    size: int
    distance_seconds: float


@dataclass(slots=True)
class RecordingProbeState:
    """Diagnostics state for explicit recording lookup tests."""

    attempted: bool = False
    success: bool = False
    event_time: datetime | None = None
    candidate_start: datetime | None = None
    candidate_end: datetime | None = None
    candidate_size: int | None = None
    candidate_distance_seconds: float | None = None
    candidate_name_present: bool = False
    failure_stage: str = ""


_PROBE_STATES: dict[str, RecordingProbeState] = {}


def probe_state(entry_id: str) -> RecordingProbeState:
    """Return the non-secret runtime state for one config entry."""
    return _PROBE_STATES.setdefault(entry_id, RecordingProbeState())


def clear_probe_state(entry_id: str) -> None:
    """Drop ephemeral probe telemetry on config-entry unload."""
    _PROBE_STATES.pop(entry_id, None)


def _interval_distance(target: datetime, start: datetime, end: datetime) -> float:
    if start <= target <= end:
        return 0.0
    if target < start:
        return (start - target).total_seconds()
    return (target - end).total_seconds()


def select_recording_candidate(
    target_local: datetime,
    files: list[VOD_file],
    *,
    tolerance: timedelta = DEFAULT_MATCH_TOLERANCE,
) -> RecordingCandidate | None:
    """Select one unique nearest recording interval within a conservative tolerance."""
    unique: dict[tuple[str, datetime, datetime], RecordingCandidate] = {}
    for vod in files:
        try:
            start = vod.start_time
            end = vod.end_time
            name = vod.file_name
            size = vod.size
        except (KeyError, TypeError, ValueError):
            continue
        # Baichuan VOD timestamps are camera-local wall-clock values. Flatten
        # timezone-aware values before comparing them with the camera-local target.
        if start.tzinfo is not None:
            start = start.replace(tzinfo=None)
        if end.tzinfo is not None:
            end = end.replace(tzinfo=None)
        distance = _interval_distance(target_local, start, end)
        candidate = RecordingCandidate(name, start, end, size, distance)
        unique[(name, start, end)] = candidate

    ranked = sorted(
        unique.values(),
        key=lambda item: (item.distance_seconds, item.start_time, item.file_name),
    )
    if not ranked or ranked[0].distance_seconds > tolerance.total_seconds():
        return None
    if len(ranked) > 1 and ranked[1].distance_seconds == ranked[0].distance_seconds:
        first = ranked[0]
        second = ranked[1]
        if (first.start_time, first.end_time, first.file_name) != (
            second.start_time,
            second.end_time,
            second.file_name,
        ):
            return None
    return ranked[0]


def _flatten_vod_files(vod_dict: dict[object, list[VOD_file]]) -> list[VOD_file]:
    files: list[VOD_file] = []
    seen: set[tuple[str, datetime, datetime]] = set()
    for values in vod_dict.values():
        for vod in values:
            try:
                key = (vod.file_name, vod.start_time, vod.end_time)
            except (KeyError, TypeError, ValueError):
                continue
            if key in seen:
                continue
            seen.add(key)
            files.append(vod)
    return files


async def async_find_recording_for_event(
    event: CloudEvent,
    uid: str,
    username: str,
    password: str,
    interface: ipaddress.IPv4Interface,
    time_zone: str,
    *,
    resolve_timeout: float = 10.0,
    command_timeout: int = 30,
) -> RecordingCandidate:
    """Open one short session, search one calendar day, then close immediately."""
    lease = None
    host = None
    connection = None
    failure_stage = "UID_RESOLVE_ERROR"
    try:
        interface_name, _ = await asyncio.to_thread(
            linux_ipv4_interface, str(interface.ip)
        )
        lease = await asyncio.to_thread(
            resolve_uid_lan, uid, interface, resolve_timeout
        )
        await asyncio.to_thread(
            validate_local_lan_route, interface, lease.host, interface_name
        )
        host = Host(
            host=lease.host,
            username=username,
            password=password,
            bc_only=True,
            bc_connection=ConnectionEnum.udp,
            uid=uid,
            timeout=command_timeout,
        )
        # Standalone Argus 2E omits channel UID metadata. Phase 2 proved the
        # main camera UID is also the correct channel-0 UID for recording queries.
        prepare_standalone_channel_zero(host)
        host._uid[0] = uid
        connection = BoundBaichuanUdpConnection(
            lease.host,
            lease.source_ip,
            0,
            host.baichuan._push_callback,
            host.baichuan._close_callback,
            uid=uid,
        )
        host.baichuan._connection = connection
        failure_stage = "WAKE_ERROR"
        await connection.connect()
        lease.close()
        lease = None

        failure_stage = "AUTH_ERROR"
        host.baichuan._first_login = False
        await host.baichuan.login()

        event_time = event.notification_post_time or event.alarm_time
        local_aware = event_time.astimezone(ZoneInfo(time_zone))
        target_local = local_aware.replace(tzinfo=None)
        day_start = datetime.combine(target_local.date(), time.min)
        day_end = datetime.combine(target_local.date(), time(23, 59, 59))

        failure_stage = "RECORDING_SEARCH_ERROR"
        _, vod_dict = await host.baichuan.search_vod_type(
            0, day_start, day_end, stream="main"
        )
        candidate = select_recording_candidate(
            target_local, _flatten_vod_files(vod_dict)
        )
        if candidate is None:
            raise CameraStageError("RECORDING_MATCH_ERROR")
        return candidate
    except CameraStageError:
        raise
    except (ReolinkError, OSError, TimeoutError):
        _LOGGER.warning("%s", failure_stage)
        raise CameraStageError(failure_stage) from None
    finally:
        try:
            if host is not None:
                try:
                    await host.logout()
                except (ReolinkError, OSError, TimeoutError):
                    pass
        finally:
            try:
                if connection is not None and connection.connection_open:
                    try:
                        await connection.close()
                    except (ReolinkError, OSError, TimeoutError):
                        pass
            finally:
                if lease is not None:
                    lease.close()
                password = ""
