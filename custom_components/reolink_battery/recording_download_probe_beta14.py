"""Controlled beta.14 cmd13 probe adding only startTime/endTime."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import recording_download_probe as base
from .recording_download_probe import (
    apply_file_info_trace,
    apply_identity_trace,
    async_prepare_download_for_event,
    download_prepare_state,
)
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = (
    "fileinfo_identity_plus_stream_type_file_type_record_type_start_end"
)


def _time_node_xml(tag: str, value: datetime) -> str:
    """Build the nested Baichuan time node used by FileInfo payloads."""
    return (
        f"<{tag}>\n"
        f"<year>{value.year}</year>"
        f"<month>{value.month}</month>"
        f"<day>{value.day}</day>"
        f"<hour>{value.hour}</hour>"
        f"<minute>{value.minute}</minute>"
        f"<second>{value.second}</second>\n"
        f"</{tag}>\n"
    )


def _download_xml_with_times(
    uid: str,
    *,
    channel_id: int,
    record_id: str,
    file_name: str,
    display_name: str,
    stream_type: str,
    file_type: str,
    record_type: str,
    start_time: datetime,
    end_time: datetime,
) -> str:
    """Keep beta.13 payload identical and add only start/end time nodes."""
    xml = base._download_xml(
        uid,
        channel_id=channel_id,
        record_id=record_id,
        file_name=file_name,
        display_name=display_name,
        stream_type=stream_type,
        file_type=file_type,
        record_type=record_type,
    )
    time_xml = _time_node_xml("startTime", start_time) + _time_node_xml(
        "endTime", end_time
    )
    marker = "</FileInfo>\n"
    if marker not in xml:
        raise RuntimeError("FILE_INFO_TIME_INSERT_ERROR")
    return xml.replace(marker, time_xml + marker, 1)


def _build_cmd13_wire(
    baichuan: Any,
    uid: str,
    candidate: RecordingCandidate,
) -> tuple[bytes, base.Cmd13RequestMetadata, base.RecordingIdentityTrace]:
    """Build beta.13 cmd13 framing and add only candidate start/end time."""
    xml_channel_id, record_id, file_name, display_name, identity_trace = (
        base._resolve_download_identity(candidate)
    )
    extension = base._binary_extension_xml().encode("utf-8")
    payload = _download_xml_with_times(
        uid,
        channel_id=xml_channel_id,
        record_id=record_id,
        file_name=file_name,
        display_name=display_name,
        stream_type=candidate.stream_type,
        file_type=candidate.file_type,
        record_type=candidate.record_type,
        start_time=candidate.start_time,
        end_time=candidate.end_time,
    ).encode("utf-8")
    encrypted_extension = baichuan._aes_encrypt(extension)
    encrypted_payload = baichuan._aes_encrypt(payload)
    body = encrypted_extension + encrypted_payload
    msg_num = base._next_download_msg_num(baichuan)
    payload_offset = len(encrypted_extension)

    header = (
        base.BAICHUAN_MAGIC
        + base.DOWNLOAD_CMD_ID.to_bytes(4, "little")
        + len(body).to_bytes(4, "little")
        + base.DOWNLOAD_HEADER_CHANNEL_ID.to_bytes(1, "little")
        + base.DOWNLOAD_STREAM_TYPE.to_bytes(1, "little")
        + msg_num.to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + base.FILE_DOWNLOAD_MESSAGE_CLASS.to_bytes(2, "little")
        + payload_offset.to_bytes(4, "little")
    )
    return (
        header + body,
        base.Cmd13RequestMetadata(
            header_channel_id=base.DOWNLOAD_HEADER_CHANNEL_ID,
            stream_type=base.DOWNLOAD_STREAM_TYPE,
            msg_num=msg_num,
            message_class=base.FILE_DOWNLOAD_MESSAGE_CLASS,
            body_length=len(body),
            payload_offset=payload_offset,
        ),
        identity_trace,
    )


# The base coroutine resolves this module global at runtime. Patch only the frame
# builder for this controlled prerelease; all search/auth/close behavior stays base.
base._build_cmd13_wire = _build_cmd13_wire
