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
    RecordingCandidate,
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
CONTENT_LAYOUT = "fileinfo_identity_plus_stream_type_file_type_record_type"


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
class RecordingIdentityTrace:
    """Secret-safe shape of the exact FileInfo identity used for cmd13."""

    id_present: bool
    file_name_present: bool
    name_present: bool
    id_length: int
    file_name_length: int
    name_length: int
    id_equals_file_name: bool | None
    id_equals_name: bool | None
    file_name_equals_name: bool | None
    id_looks_like_path: bool
    file_name_looks_like_path: bool
    xml_channel_id_present: bool
    xml_channel_id_value: int | None
    stream_type_present: bool
    stream_type_value: str | None
    file_type_present: bool
    file_type_value: str | None
    record_type_present: bool
    record_type_value: str | None
    used_exact_id: bool
    used_exact_file_name: bool
    used_exact_name: bool


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
    identity_trace: RecordingIdentityTrace


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
    identity_id_present: bool = False
    identity_file_name_present: bool = False
    identity_name_present: bool = False
    identity_id_length: int = 0
    identity_file_name_length: int = 0
    identity_name_length: int = 0
    identity_id_equals_file_name: bool | None = None
    identity_id_equals_name: bool | None = None
    identity_file_name_equals_name: bool | None = None
    identity_id_looks_like_path: bool = False
    identity_file_name_looks_like_path: bool = False
    identity_xml_channel_id_present: bool = False
    identity_xml_channel_id_value: int | None = None
    identity_stream_type_present: bool = False
    identity_stream_type_value: str | None = None
    identity_file_type_present: bool = False
    identity_file_type_value: str | None = None
    identity_record_type_present: bool = False
    identity_record_type_value: str | None = None
    identity_used_exact_id: bool = False
    identity_used_exact_file_name: bool = False
    identity_used_exact_name: bool = False


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


def apply_identity_trace(
    state: DownloadPrepareState, trace: RecordingIdentityTrace | None
) -> None:
    """Copy only non-identifying FileInfo identity shape into diagnostics state."""
    if trace is None:
        return
    state.identity_id_present = trace.id_present
    state.identity_file_name_present = trace.file_name_present
    state.identity_name_present = trace.name_present
    state.identity_id_length = trace.id_length
    state.identity_file_name_length = trace.file_name_length
    state.identity_name_length = trace.name_length
    state.identity_id_equals_file_name = trace.id_equals_file_name
    state.identity_id_equals_name = trace.id_equals_name
    state.identity_file_name_equals_name = trace.file_name_equals_name
    state.identity_id_looks_like_path = trace.id_looks_like_path
    state.identity_file_name_looks_like_path = trace.file_name_looks_like_path
    state.identity_xml_channel_id_present = trace.xml_channel_id_present
    state.identity_xml_channel_id_value = trace.xml_channel_id_value
    state.identity_stream_type_present = trace.stream_type_present
    state.identity_stream_type_value = trace.stream_type_value
    state.identity_file_type_present = trace.file_type_present
    state.identity_file_type_value = trace.file_type_value
    state.identity_record_type_present = trace.record_type_present
    state.identity_record_type_value = trace.record_type_value
    state.identity_used_exact_id = trace.used_exact_id
    state.identity_used_exact_file_name = trace.used_exact_file_name
    state.identity_used_exact_name = trace.used_exact_name


def _eq_if_present(left: str, right: str) -> bool | None:
    return left == right if left and right else None


def _looks_like_path(value: str) -> bool:
    return bool(value) and ("/" in value or "\\" in value)


def _resolve_download_identity(
    candidate: RecordingCandidate,
) -> tuple[int, str, str, str, RecordingIdentityTrace]:
    """Resolve cmd13 fields while preserving exact FileInfo values when available."""
    exact_id = candidate.record_id
    exact_file_name = candidate.xml_file_name
    exact_name = candidate.display_name

    record_id = exact_id or candidate.file_name
    file_name = exact_file_name or candidate.file_name
    display_name = (
        exact_name
        or os.path.basename(file_name.replace("\\", "/"))
        or file_name
    )

    raw_channel = candidate.channel_id
    xml_channel_id = (
        raw_channel
        if isinstance(raw_channel, int) and 0 <= raw_channel <= 255
        else 0
    )

    trace = RecordingIdentityTrace(
        id_present=bool(exact_id),
        file_name_present=bool(exact_file_name),
        name_present=bool(exact_name),
        id_length=len(exact_id),
        file_name_length=len(exact_file_name),
        name_length=len(exact_name),
        id_equals_file_name=_eq_if_present(exact_id, exact_file_name),
        id_equals_name=_eq_if_present(exact_id, exact_name),
        file_name_equals_name=_eq_if_present(exact_file_name, exact_name),
        id_looks_like_path=_looks_like_path(exact_id),
        file_name_looks_like_path=_looks_like_path(exact_file_name),
        xml_channel_id_present=raw_channel is not None,
        xml_channel_id_value=raw_channel if isinstance(raw_channel, int) else None,
        stream_type_present=bool(candidate.stream_type),
        stream_type_value=candidate.stream_type or None,
        file_type_present=bool(candidate.file_type),
        file_type_value=candidate.file_type or None,
        record_type_present=bool(candidate.record_type),
        record_type_value=candidate.record_type or None,
        used_exact_id=bool(exact_id),
        used_exact_file_name=bool(exact_file_name),
        used_exact_name=bool(exact_name),
    )
    return xml_channel_id, record_id, file_name, display_name, trace


def _download_xml(
    uid: str,
    *,
    channel_id: int,
    record_id: str,
    file_name: str,
    display_name: str,
    stream_type: str = "",
    file_type: str = "",
    record_type: str = "",
) -> str:
    """Build FileInfoList download body from selected FileInfo metadata."""
    stream_type_xml = (
        f'<streamType>{escape(stream_type)}</streamType>\n' if stream_type else ""
    )
    file_type_xml = f'<fileType>{escape(file_type)}</fileType>\n' if file_type else ""
    record_type_xml = (
        f'<recordType>{escape(record_type)}</recordType>\n' if record_type else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<body>\n<FileInfoList version="1.1">\n<FileInfo>\n'
        f'<channelId>{channel_id}</channelId>\n'
        f'<uid>{escape(uid)}</uid>\n'
        f'<fileName>{escape(file_name)}</fileName>\n'
        f'<name>{escape(display_name)}</name>\n'
        f'<Id>{escape(record_id)}</Id>\n'
        f'{stream_type_xml}'
        f'{file_type_xml}'
        f'{record_type_xml}'
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
    baichuan: Any,
    uid: str,
    candidate: RecordingCandidate,
) -> tuple[bytes, Cmd13RequestMetadata, RecordingIdentityTrace]:
    """Build cmd13 with beta.12 framing plus exact recordType metadata."""
    xml_channel_id, record_id, file_name, display_name, identity_trace = (
        _resolve_download_identity(candidate)
    )
    extension = _binary_extension_xml().encode("utf-8")
    payload = _download_xml(
        uid,
        channel_id=xml_channel_id,
        record_id=record_id,
        file_name=file_name,
        display_name=display_name,
        stream_type=candidate.stream_type,
        file_type=candidate.file_type,
        record_type=candidate.record_type,
    ).encode("utf-8")
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
    return (
        header + body,
        Cmd13RequestMetadata(
            header_channel_id=DOWNLOAD_HEADER_CHANNEL_ID,
            stream_type=DOWNLOAD_STREAM_TYPE,
            msg_num=msg_num,
            message_class=FILE_DOWNLOAD_MESSAGE_CLASS,
            body_length=len(body),
            payload_offset=payload_offset,
        ),
        identity_trace,
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
    """Find the event recording, send one metadata-enriched cmd13 probe, then close."""
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
        wire, request, identity_trace = _build_cmd13_wire(host.baichuan, uid, candidate)
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
            identity_trace=identity_trace,
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