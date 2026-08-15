"""Battery-safe manual recording lookup for Milestone 3B.2a."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from xml.etree import ElementTree as XML
from zoneinfo import ZoneInfo

from reolink_aio.api import Host
from reolink_aio.baichuan import xmls
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError
from reolink_aio.typings import parse_file_name

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
FILE_INFO_HEADER_CHANNEL_ID = 7
MAX_FILE_INFO_PAGES = 10
TYPICAL_FILE_INFO_PAGE_SIZE = 40
# A freshly authenticated battery camera can transiently reject cmd14 while its
# SD/recording service is still becoming ready. Retry only cmd14 and only while
# the already authenticated Baichuan session remains open; do not wake/reconnect.
FILE_INFO_OPEN_RETRY_DELAYS_SECONDS = (1.5, 2.5)


@dataclass(frozen=True, slots=True)
class RecordingMetadata:
    """One SD recording returned by FileInfoList, preserving download identity."""

    file_name: str
    start_time: datetime
    end_time: datetime
    size: int
    record_id: str = ""
    xml_file_name: str = ""
    display_name: str = ""
    channel_id: int | None = None
    stream_type: str = ""
    file_type: str = ""
    record_type: str = ""


@dataclass(frozen=True, slots=True)
class RecordingCandidate:
    """Selected SD recording plus exact FileInfo identity fields."""

    file_name: str
    start_time: datetime
    end_time: datetime
    size: int
    distance_seconds: float
    record_id: str = ""
    xml_file_name: str = ""
    display_name: str = ""
    channel_id: int | None = None
    stream_type: str = ""
    file_type: str = ""
    record_type: str = ""


@dataclass(slots=True)
class FileInfoTrace:
    """Secret-safe telemetry for one cmd14/cmd15/cmd16 transaction."""

    open_attempted: bool = False
    open_succeeded: bool = False
    open_failure_type: str = ""
    open_response_code: int | None = None
    handle_present: bool = False
    get_attempted: bool = False
    get_page_index: int | None = None
    get_pages_succeeded: int = 0
    get_failure_type: str = ""
    get_response_code: int | None = None
    last_page_file_count: int | None = None
    finished_flag: bool | None = None
    close_attempted: bool = False
    close_succeeded: bool = False
    close_failure_type: str = ""
    close_response_code: int | None = None


class FileInfoListError(CameraStageError):
    """FileInfoList failure carrying only non-secret protocol telemetry."""

    def __init__(self, stage: str, trace: FileInfoTrace) -> None:
        super().__init__(stage)
        self.trace = trace
        if stage == "FILE_INFO_OPEN_ERROR":
            self.failure_type = trace.open_failure_type
            self.response_code = trace.open_response_code
        elif stage == "FILE_INFO_GET_ERROR":
            self.failure_type = trace.get_failure_type
            self.response_code = trace.get_response_code
        else:
            self.failure_type = ""
            self.response_code = None


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


def _rsp_code(err: BaseException) -> int | None:
    value = getattr(err, "rspCode", None)
    return value if isinstance(value, int) else None


def _interval_distance(target: datetime, start: datetime, end: datetime) -> float:
    if start <= target <= end:
        return 0.0
    if target < start:
        return (start - target).total_seconds()
    return (target - end).total_seconds()


def select_recording_candidate(
    target_local: datetime,
    files: list[object],
    *,
    tolerance: timedelta = DEFAULT_MATCH_TOLERANCE,
) -> RecordingCandidate | None:
    """Select one unique nearest recording interval within a conservative tolerance."""
    unique: dict[tuple[str, datetime, datetime], RecordingCandidate] = {}
    for vod in files:
        try:
            start = vod.start_time  # type: ignore[attr-defined]
            end = vod.end_time  # type: ignore[attr-defined]
            name = vod.file_name  # type: ignore[attr-defined]
            size = vod.size  # type: ignore[attr-defined]
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if start.tzinfo is not None:
            start = start.replace(tzinfo=None)
        if end.tzinfo is not None:
            end = end.replace(tzinfo=None)
        distance = _interval_distance(target_local, start, end)
        candidate = RecordingCandidate(
            file_name=name,
            start_time=start,
            end_time=end,
            size=int(size),
            distance_seconds=distance,
            record_id=str(getattr(vod, "record_id", "") or ""),
            xml_file_name=str(getattr(vod, "xml_file_name", "") or ""),
            display_name=str(getattr(vod, "display_name", "") or ""),
            channel_id=getattr(vod, "channel_id", None),
            stream_type=str(getattr(vod, "stream_type", "") or ""),
            file_type=str(getattr(vod, "file_type", "") or ""),
            record_type=str(getattr(vod, "record_type", "") or ""),
        )
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


def _text(node: XML.Element, *names: str) -> str | None:
    for name in names:
        value = node.findtext(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _xml_time(node: XML.Element, tag: str) -> datetime | None:
    block = node.find(tag)
    if block is None:
        return None
    try:
        return datetime(
            int(block.findtext("year", "")),
            int(block.findtext("month", "")),
            int(block.findtext("day", "")),
            int(block.findtext("hour", "")),
            int(block.findtext("minute", "")),
            int(block.findtext("second", "")),
        )
    except (TypeError, ValueError):
        return None


def _metadata_from_file_info(node: XML.Element) -> RecordingMetadata | None:
    # Keep these fields distinct. The official SDK download API consumes recording
    # identity and filename separately, so collapsing them can produce a validly
    # framed cmd13 request that the camera rejects as a bad file selection.
    record_id = _text(node, "Id", "ID", "id") or ""
    xml_file_name = _text(node, "fileName") or ""
    display_name = _text(node, "name") or ""
    chosen = record_id or xml_file_name or display_name
    if not chosen:
        return None

    start = _xml_time(node, "startTime")
    end = _xml_time(node, "endTime")
    if start is None or end is None:
        parsed = None
        for candidate_name in (display_name, xml_file_name, record_id, chosen):
            if candidate_name:
                parsed = parse_file_name(candidate_name)
                if parsed is not None:
                    break
        if parsed is not None:
            start = datetime.combine(parsed.date, parsed.start)
            end = datetime.combine(parsed.date, parsed.end)
            if end < start:
                end += timedelta(days=1)
    if start is None or end is None:
        return None

    size_text = _text(node, "size", "fileSize")
    try:
        size = int(size_text) if size_text else 0
    except ValueError:
        size = 0

    channel_text = _text(node, "channelId")
    try:
        channel_id = int(channel_text) if channel_text is not None else None
    except ValueError:
        channel_id = None

    return RecordingMetadata(
        file_name=chosen,
        start_time=start,
        end_time=end,
        size=size,
        record_id=record_id,
        xml_file_name=xml_file_name,
        display_name=display_name,
        channel_id=channel_id,
        stream_type=_text(node, "streamType") or "",
        file_type=_text(node, "fileType") or "",
        record_type=_text(node, "recordType", "type", "alarmType") or "",
    )


def _parse_file_info_page(xml_text: str) -> tuple[list[RecordingMetadata], bool | None]:
    try:
        root = XML.fromstring(xml_text)
    except XML.ParseError as err:
        raise CameraStageError("FILE_INFO_PARSE_ERROR") from err

    files: list[RecordingMetadata] = []
    for node in root.findall(".//FileInfo"):
        item = _metadata_from_file_info(node)
        if item is not None:
            files.append(item)

    finished_text = root.findtext(".//bFinished")
    if finished_text is None:
        finished_text = root.findtext(".//finished")
    finished = None if finished_text is None else finished_text.strip() == "1"
    return files, finished


async def _list_recordings_file_info(
    host: Host,
    uid: str,
    start: datetime,
    end: datetime,
    *,
    trace: FileInfoTrace | None = None,
) -> list[RecordingMetadata]:
    """Use the cmd14/cmd15/cmd16 tuple already validated on this Argus 2E."""
    trace = trace or FileInfoTrace()
    open_xml = xmls.FileInfoListOpen.format(
        uid=uid,
        channel=0,
        start_year=start.year,
        start_month=start.month,
        start_day=start.day,
        start_hour=start.hour,
        start_minute=start.minute,
        start_second=start.second,
        end_year=end.year,
        end_month=end.month,
        end_day=end.day,
        end_hour=end.hour,
        end_minute=end.minute,
        end_second=end.second,
    )
    trace.open_attempted = True
    open_response: str | None = None
    for attempt in range(len(FILE_INFO_OPEN_RETRY_DELAYS_SECONDS) + 1):
        try:
            open_response = await host.baichuan.send(
                cmd_id=14,
                body=open_xml,
                ch_id=FILE_INFO_HEADER_CHANNEL_ID,
            )
            trace.open_succeeded = True
            break
        except (ReolinkError, OSError, TimeoutError) as err:
            trace.open_failure_type = type(err).__name__
            trace.open_response_code = _rsp_code(err)
            if attempt >= len(FILE_INFO_OPEN_RETRY_DELAYS_SECONDS):
                raise FileInfoListError("FILE_INFO_OPEN_ERROR", trace) from None
            connection = getattr(host.baichuan, "_connection", None)
            if connection is not None and not getattr(connection, "connection_open", False):
                raise FileInfoListError("FILE_INFO_OPEN_ERROR", trace) from None
            await asyncio.sleep(FILE_INFO_OPEN_RETRY_DELAYS_SECONDS[attempt])

    if open_response is None:
        raise FileInfoListError("FILE_INFO_OPEN_ERROR", trace)

    try:
        open_root = XML.fromstring(open_response)
        handle_text = open_root.findtext(".//handle")
        handle = int(handle_text) if handle_text else None
    except (XML.ParseError, TypeError, ValueError):
        handle = None
    trace.handle_present = handle is not None
    if handle is None:
        raise FileInfoListError("FILE_INFO_HANDLE_ERROR", trace)

    page_xml = xmls.FileInfoList.format(channel=0, handle=handle, uid=uid)
    recordings: list[RecordingMetadata] = []
    seen_names: set[str] = set()
    try:
        for page_index in range(MAX_FILE_INFO_PAGES):
            trace.get_attempted = True
            trace.get_page_index = page_index
            try:
                response = await host.baichuan.send(
                    cmd_id=15,
                    body=page_xml,
                    ch_id=FILE_INFO_HEADER_CHANNEL_ID,
                )
            except (ReolinkError, OSError, TimeoutError) as err:
                trace.get_failure_type = type(err).__name__
                trace.get_response_code = _rsp_code(err)
                # Some firmwares signal end-of-list with a 400 after at least one page.
                if page_index > 0 and trace.get_response_code == 400:
                    break
                raise FileInfoListError("FILE_INFO_GET_ERROR", trace) from None

            trace.get_pages_succeeded += 1
            try:
                page_files, finished = _parse_file_info_page(response)
            except CameraStageError:
                raise FileInfoListError("FILE_INFO_PARSE_ERROR", trace) from None
            trace.last_page_file_count = len(page_files)
            trace.finished_flag = finished
            for item in page_files:
                if item.file_name in seen_names:
                    continue
                seen_names.add(item.file_name)
                recordings.append(item)

            if finished is True:
                break
            if not page_files or (
                finished is None and len(page_files) < TYPICAL_FILE_INFO_PAGE_SIZE
            ):
                break
    finally:
        trace.close_attempted = True
        try:
            await host.baichuan.send(
                cmd_id=16,
                body=page_xml,
                ch_id=FILE_INFO_HEADER_CHANNEL_ID,
                retry=1,
            )
            trace.close_succeeded = True
        except (ReolinkError, OSError, TimeoutError) as err:
            trace.close_failure_type = type(err).__name__
            trace.close_response_code = _rsp_code(err)
            _LOGGER.debug("FILE_INFO_CLOSE_ERROR")

    return recordings


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

        recordings = await _list_recordings_file_info(host, uid, day_start, day_end)
        candidate = select_recording_candidate(target_local, list(recordings))
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