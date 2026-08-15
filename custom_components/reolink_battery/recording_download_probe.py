"""Battery-safe cmd13 preparation probe for Milestone 3B.2b."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time
from xml.etree import ElementTree as XML
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reolink_aio.api import Host
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError

from .camera import CameraStageError, prepare_standalone_channel_zero
from .events import CloudEvent
from .recording_probe import (
    FILE_INFO_HEADER_CHANNEL_ID,
    _list_recordings_file_info,
    select_recording_candidate,
)
from .transport import (
    BoundBaichuanUdpConnection,
    linux_ipv4_interface,
    resolve_uid_lan,
    validate_local_lan_route,
)

_LOGGER = logging.getLogger(__name__)
DOWNLOAD_HEADER_CHANNEL_ID = FILE_INFO_HEADER_CHANNEL_ID


class DownloadPrepareError(CameraStageError):
    """Secret-safe cmd13 failure with non-sensitive protocol metadata."""

    def __init__(
        self,
        stage: str,
        *,
        failure_type: str = "",
        response_code: int | None = None,
    ) -> None:
        super().__init__(stage)
        self.failure_type = failure_type
        self.response_code = response_code


@dataclass(frozen=True, slots=True)
class DownloadPrepareResult:
    """Non-secret metadata returned after selecting one recording with cmd13."""

    candidate_start: datetime
    candidate_end: datetime
    candidate_distance_seconds: float
    response_present: bool
    handle_present: bool
    expected_size: int | None
    response_file_name_present: bool


@dataclass(slots=True)
class DownloadPrepareState:
    """Ephemeral diagnostics for the explicit cmd13 validation."""

    attempted: bool = False
    success: bool = False
    event_time: datetime | None = None
    candidate_start: datetime | None = None
    candidate_end: datetime | None = None
    candidate_distance_seconds: float | None = None
    response_present: bool = False
    handle_present: bool = False
    expected_size: int | None = None
    response_file_name_present: bool = False
    failure_stage: str = ""
    failure_type: str = ""
    response_code: int | None = None


_STATES: dict[str, DownloadPrepareState] = {}


def download_prepare_state(entry_id: str) -> DownloadPrepareState:
    """Return secret-safe cmd13 probe state for one config entry."""
    return _STATES.setdefault(entry_id, DownloadPrepareState())


def _download_xml(uid: str, file_name: str) -> str:
    basename = os.path.basename(file_name.replace("\\", "/")) or file_name
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<body>\n<FileInfoList version="1.1">\n<FileInfo>\n'
        '<channelId>0</channelId>\n'
        f'<uid>{escape(uid)}</uid>\n'
        f'<fileName>{escape(file_name)}</fileName>\n'
        f'<name>{escape(basename)}</name>\n'
        f'<Id>{escape(file_name)}</Id>\n'
        '</FileInfo>\n</FileInfoList>\n</body>'
    )


def _binary_extension_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<Extension version="1.1">\n'
        '<binaryData>1</binaryData>\n'
        '<channelId>0</channelId>\n'
        '</Extension>'
    )


def _parse_prepare_response(response: str) -> tuple[bool, bool, int | None, bool]:
    if not response.strip():
        return False, False, None, False
    try:
        root = XML.fromstring(response)
    except XML.ParseError as err:
        raise DownloadPrepareError("DOWNLOAD_PREPARE_PARSE_ERROR") from err

    handle_present = bool(root.findtext(".//handle"))
    file_name_present = any(
        bool(root.findtext(path))
        for path in (".//fileName", ".//name", ".//Id", ".//ID")
    )
    size_text = root.findtext(".//size") or root.findtext(".//fileSize")
    expected_size = None
    if size_text:
        try:
            parsed_size = int(size_text)
        except ValueError:
            parsed_size = 0
        if parsed_size > 0:
            expected_size = parsed_size
    return True, handle_present, expected_size, file_name_present


async def async_prepare_download_for_event(
    event: CloudEvent,
    uid: str,
    username: str,
    password: str,
    interface: ipaddress.IPv4Interface,
    time_zone: str,
    *,
    resolve_timeout: float = 10.0,
    command_timeout: int = 30,
) -> DownloadPrepareResult:
    """Find the event recording and validate cmd13 without starting media transfer."""
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
        target_local = event_time.astimezone(ZoneInfo(time_zone)).replace(tzinfo=None)
        day_start = datetime.combine(target_local.date(), time.min)
        day_end = datetime.combine(target_local.date(), time(23, 59, 59))

        recordings = await _list_recordings_file_info(host, uid, day_start, day_end)
        candidate = select_recording_candidate(target_local, list(recordings))
        if candidate is None:
            raise DownloadPrepareError("RECORDING_MATCH_ERROR")

        failure_stage = "DOWNLOAD_PREPARE_ERROR"
        try:
            response = await host.baichuan.send(
                cmd_id=13,
                body=_download_xml(uid, candidate.file_name),
                extension=_binary_extension_xml(),
                ch_id=DOWNLOAD_HEADER_CHANNEL_ID,
                message_class="1464",
                retry=1,
            )
        except (ReolinkError, OSError, TimeoutError) as err:
            rsp_code = getattr(err, "rspCode", None)
            if not isinstance(rsp_code, int):
                rsp_code = None
            raise DownloadPrepareError(
                "DOWNLOAD_PREPARE_ERROR",
                failure_type=type(err).__name__,
                response_code=rsp_code,
            ) from None

        response_present, handle_present, expected_size, file_name_present = (
            _parse_prepare_response(response)
        )
        return DownloadPrepareResult(
            candidate.start_time,
            candidate.end_time,
            candidate.distance_seconds,
            response_present,
            handle_present,
            expected_size,
            file_name_present,
        )
    except CameraStageError:
        raise
    except (ReolinkError, OSError, TimeoutError) as err:
        _LOGGER.warning("%s", failure_stage)
        rsp_code = getattr(err, "rspCode", None)
        if not isinstance(rsp_code, int):
            rsp_code = None
        raise DownloadPrepareError(
            failure_stage,
            failure_type=type(err).__name__,
            response_code=rsp_code,
        ) from None
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
