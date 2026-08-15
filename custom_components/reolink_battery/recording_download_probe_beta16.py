"""Controlled beta.16 cmd13 probe using Id-only FileInfo without Extension."""

from __future__ import annotations

from typing import Any

from . import recording_download_probe as base
from .recording_download_probe import (
    apply_file_info_trace,
    apply_identity_trace,
    async_prepare_download_for_event,
    download_prepare_state,
)
from .recording_download_probe_beta15 import _download_xml_id_only
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = "fileinfo_id_only_no_extension"


def _build_cmd13_wire(
    baichuan: Any,
    uid: str,
    candidate: RecordingCandidate,
) -> tuple[bytes, base.Cmd13RequestMetadata, base.RecordingIdentityTrace]:
    """Keep beta.15 Id-only payload and remove only the binary Extension."""
    del uid  # Intentionally omitted from the public Id-only FileInfo strategy.
    xml_channel_id, record_id, _file_name, _display_name, identity_trace = (
        base._resolve_download_identity(candidate)
    )
    payload = _download_xml_id_only(
        channel_id=xml_channel_id,
        record_id=record_id,
    ).encode("utf-8")

    # With no Extension, the public 0x6482 encoder still encrypts the XML payload
    # and emits the 24-byte modern header with payload_offset=0.
    body = baichuan._aes_encrypt(payload)
    msg_num = base._next_download_msg_num(baichuan)
    payload_offset = 0

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
