"""Controlled beta.15 cmd13 probe using only channelId + Id in FileInfo."""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

from . import recording_download_probe as base
from .recording_download_probe import (
    apply_file_info_trace,
    apply_identity_trace,
    async_prepare_download_for_event,
    download_prepare_state,
)
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = "fileinfo_id_only"


def _download_xml_id_only(*, channel_id: int, record_id: str) -> str:
    """Build the minimal FileInfo payload used by the public id strategy."""
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<body>\n'
        '<FileInfoList version="1.1">\n'
        '<FileInfo>\n'
        f'<channelId>{channel_id}</channelId>\n'
        f'<Id>{escape(record_id)}</Id>\n'
        '</FileInfo>\n'
        '</FileInfoList>\n'
        '</body>'
    )


def _build_cmd13_wire(
    baichuan: Any,
    uid: str,
    candidate: RecordingCandidate,
) -> tuple[bytes, base.Cmd13RequestMetadata, base.RecordingIdentityTrace]:
    """Keep beta.14 framing/Extension, but reduce only the FileInfo body to Id."""
    del uid  # Intentionally omitted from the id-only FileInfo strategy.
    xml_channel_id, record_id, _file_name, _display_name, identity_trace = (
        base._resolve_download_identity(candidate)
    )
    extension = base._binary_extension_xml().encode("utf-8")
    payload = _download_xml_id_only(
        channel_id=xml_channel_id,
        record_id=record_id,
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
# builder for this controlled prerelease; search/auth/close behavior stays base.
base._build_cmd13_wire = _build_cmd13_wire
