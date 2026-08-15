"""Battery-safe cmd13 frame probe for Milestone 3B.2b."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reolink_aio.api import Host
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError

from .camera import CameraStageError, prepare_standalone_channel_zero
from .events import CloudEvent
from .recording_probe import (
    FILE_INFO_HEADER_CHANNEL_ID,
    FileInfoListError,
    FileInfoTrace,
    _list_recordings_file_info,
    select_recording_candidate,
)
from .transport import (
    BAICHUAN_MAGIC,
    FILE_DOWNLOAD_MESSAGE_CLASS,
    BoundBaichuanUdpConnection,
    FileDownloadFrameMetadata,
    linux_ipv4_interface,
    resolve_uid_lan,
    validate_local_lan_route,
)

_LOGGER = logging.getLogger(__name__)
DOWNLOAD_CMD_ID = 13
# The official Argus SDK oracle reported channel_id=7, stream_type=0 for this
# recording path. Keep those two fields independent from the 16-bit msgNum.
DOWNLOAD_HEADER_CHANNEL_ID = FILE_INFO_HEADER_CHANNEL_ID
DOWNLOAD_STREAM_TYPE = 0
MESSAGE_NUM_MODULUS = 1 << 16
ACCEPTED_PREPARE_RESPONSE_CODES = frozenset({0, 200, 201, 300})
ROUTING_LAYOUT = "file_download_ch_stream_msgnum16"


class DownloadPrepareError(CameraStageError):
    """Secret-safe cmd13 failure with non-sensitive protocol metadata."""

    def __init__(
        self,
        stage: str,
        *,
        failure_type: str = "",
        response_code: int | None = None,
        file_info_trace: FileInfoTrace | None = None,
    ) -> None:
        super().__init__(stage)
        self.failure_type = failure_type
        self.response_code = response_code
        self.file_info_trace = file_info_trace


@dataclass(frozen=True, slots=True)
class Cmd13RequestMetadata:
    """Non-secret metadata for the one raw cmd13 request."""

    header_channel_id: int
    stream_type: int
    msg_num: int
    message_class: int
    body_length: int
    payload_offset: int


@dataclass(frozen=True, slots=True)
class DownloadPrepareResult:
    """Secret-safe result after sending one correctly framed cmd13 request."""

    candidate_start: datetime
    candidate_end: datetime
    candidate_distance_seconds: float
    request: Cmd13RequestMetadata
    response: FileDownloadFrameMetadata
    response_accepted: bool
    file_info_trace: FileInfoTrace


@dataclass(slots=True)
class DownloadPrepareState:
    """Ephemeral diagnostics for the explicit cmd13 frame validation."""

    attempted: bool = False
    success: bool = False
    event_time: datetime | None = None
    candidate_start: datetime | None = None
    candidate_end: datetime | None = None
    candidate_distance_seconds: float | None = None
    response_present: bool = False
    response_accepted: bool = False
    failure_stage: str = ""
    failure_type: str = ""
    response_code: int | None = None
    request_header_channel_id: int | None = None
    request_stream_type: int | None = None
    request_msg_num: int | None = None
    request_message_class: int | None = None
    request_body_length: int | None = None
    request_payload_offset: int | None = None
    response_message_class: int | None = None
    response_header_channel_id: int | None = None
    response_stream_type: int | None = None
    response_msg_num: int | None = None
    response_body_length: int | None = None
    response_payload_offset: int | None = None
    first_payload_length: int = 0
    file_info_open_attempted: bool = False
    file_info_open_succeeded: bool = False
    file_info_open_failure_type: str = ""
    file_info_open_response_code: int | None = None
    file_info_handle_present: bool = False
    file_info_get_attempted: bool = False
    file_info_get_page_index: int | None = None
    file_info_get_pages_succeeded: int = 0
    file_info_get_failure_type: str = ""
    file_info_get_response_code: int | None = None
    file_info_last_page_file_count: int | None = None
    file_info_finished_flag: bool | None = None
    file_info_close_attempted: bool = False
    file_info_close_succeeded: bool = False
    file_info_close_failure_type: str = ""
    file_info_close_response_code: int | None = None


_STATES: dict[str, DownloadPrepareState] = {}


def download_prepare_state(entry_id: str) -> DownloadPrepareState:
    """Return secret-safe cmd13 probe state for one config entry."""
    return _STATES.setdefault(entry_id, DownloadPrepareState())


def apply_file_info_trace(state: DownloadPrepareState, trace: FileInfoTrace | None) -> None:
    """Copy only non-secret FileInfoList telemetry into runtime diagnostics state."""
    if trace is None:
        return
    state.file_info_open_attempted = trace.open_attempted
    state.file_info_open_succeeded = trace.open_succeeded
    state.file_info_open_failure_type = trace.open_failure_type
    state.file_info_open_response_code = trace.open_response_code
    state.file_info_handle_present = trace.handle_present
    state.file_info_get_attempted = trace.get_attempted
    state.file_info_get_page_index = trace.get_page_index
    state.file_info_get_pages_succeeded = trace.get_pages_succeeded
    state.file_info_get_failure_type = trace.get_failure_type
    state.file_info_get_response_code = trace.get_response_code
    state.file_info_last_page_file_count = trace.last_page_file_count
    state.file_info_finished_flag = trace.finished_flag
    state.file_info_close_attempted = trace.close_attempted
    state.file_info_close_succeeded = trace.close_succeeded
    state.file_info_close_failure_type = trace.close_failure_type
    state.file_info_close_response_code = trace.close_response_code


def _download_xml(uid: str, file_name: str) -> str:
    """Keep the proven FileInfoList download body unchanged."""
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
    """Keep the proven binary Extension unchanged."""
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<Extension version="1.1">\n'
        '<binaryData>1</binaryData>\n'
        '<channelId>0</channelId>\n'
        '</Extension>'
    )


def _next_download_msg_num(baichuan: Any) -> int:
    """Allocate the 16-bit msgNum used by the dedicated file-download header."""
    current = int(getattr(baichuan, "_mess_id", 0))
    msg_num = (current + 1) % MESSAGE_NUM_MODULUS
    baichuan._mess_id = msg_num
    return msg_num


def _build_cmd13_wire(
    baichuan: Any, uid: str, file_name: str
) -> tuple[bytes, Cmd13RequestMetadata]:
    """Build cmd13 as channelId + streamType + msgNum16, with channelId 7."""
    extension = _binary_extension_xml().encode("utf-8")
    payload = _download_xml(uid, file_name).encode("utf-8")
    encrypted_extension = baichuan._aes_encrypt(extension)
    encrypted_payload = baichuan._aes_encrypt(payload)
    body = encrypted_extension + encrypted_payload
    msg_num = _next_download_msg_num(baichuan)
    payload_offset = len(encrypted_extension)

    header = (
        BAICHUAN_MAGIC
        + DOWNLOAD_CMD_ID.to_bytes(4, "little")
        + len(body).to_bytes(4, "little")
        + DOWNLOAD_HEADER_CHANNEL_ID.to_bytes(1, "little")
        + DOWNLOAD_STREAM_TYPE.to_bytes(1, "little")
        + msg_num.to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + FILE_DOWNLOAD_MESSAGE_CLASS.to_bytes(2, "little")
        + payload_offset.to_bytes(4, "little")
    )
    return header + body, Cmd13RequestMetadata(
        header_channel_id=DOWNLOAD_HEADER_CHANNEL_ID,
        stream_type=DOWNLOAD_STREAM_TYPE,
        msg_num=msg_num,
        message_class=FILE_DOWNLOAD_MESSAGE_CLASS,
        body_length=len(body),
        payload_offset=payload_offset,
    )


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
    """Find the event recording, send one cmd13 routing probe, then close."""
    lease = None
    host = None
    connection = None
    failure_stage = "UID_RESOLVE_ERROR"
    file_info_trace = FileInfoTrace()
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

        try:
            recordings = await _list_recordings_file_info(
                host, uid, day_start, day_end, trace=file_info_trace
            )
        except FileInfoListError as err:
            raise DownloadPrepareError(
                err.stage,
                failure_type=err.failure_type,
                response_code=err.response_code,
                file_info_trace=err.trace,
            ) from None

        candidate = select_recording_candidate(target_local, list(recordings))
        if candidate is None:
            raise DownloadPrepareError(
                "RECORDING_MATCH_ERROR", file_info_trace=file_info_trace
            )

        failure_stage = "DOWNLOAD_PREPARE_FRAME_ERROR"
        wire, request = _build_cmd13_wire(host.baichuan, uid, candidate.file_name)
        try:
            response = await connection.send_file_download_probe(
                wire,
                expected_msg_num=request.msg_num,
                timeout=min(float(command_timeout), 15.0),
            )
        except TimeoutError:
            raise DownloadPrepareError(
                "DOWNLOAD_PREPARE_TIMEOUT",
                failure_type="TimeoutError",
                file_info_trace=file_info_trace,
            ) from None
        except (ReolinkError, OSError, RuntimeError) as err:
            raise DownloadPrepareError(
                "DOWNLOAD_PREPARE_FRAME_ERROR",
                failure_type=type(err).__name__,
                file_info_trace=file_info_trace,
            ) from None

        return DownloadPrepareResult(
            candidate_start=candidate.start_time,
            candidate_end=candidate.end_time,
            candidate_distance_seconds=candidate.distance_seconds,
            request=request,
            response=response,
            response_accepted=response.response_code
            in ACCEPTED_PREPARE_RESPONSE_CODES,
            file_info_trace=file_info_trace,
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
            file_info_trace=file_info_trace,
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
