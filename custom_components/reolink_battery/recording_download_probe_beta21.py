"""Beta.21: proven cmd13 prepare followed by handle-bound full-high cmd8.

The accepted Argus path is kept for cmd13: class 0x6482, header channel 7,
stream 0, Id-only encrypted XML, no Extension and payload_offset 0.  Only after
that accepted cmd13 XML is received, this beta extracts the returned handle
privately and builds cmd8 with the public full-high FileInfo shape, explicitly
requesting mainStream.  Transport/P2P heartbeat behavior is inherited unchanged
from beta.20.  No recording file is written and no cmd9 is sent.
"""

from __future__ import annotations

import asyncio
import copy
import os
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from . import recording_download_probe as base
from . import recording_download_probe_beta17 as beta17
from . import recording_download_probe_beta20 as beta20
from .recording_download_probe_beta16 import (
    _build_cmd13_wire as _beta16_build_cmd13_wire,
)
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = "cmd13_id_prepare_then_cmd8_full_high_handle_mainstream"
STREAM_SAMPLE_MAX_BYTES = 16 * 1024 * 1024
STREAM_SAMPLE_MAX_FRAMES = 4096


@dataclass(slots=True)
class FullHighProbeTrace(beta20.P2PHeartbeatProbeTrace):
    """Secret-safe telemetry for the handle-bound full-high cmd8 experiment."""

    cmd8_full_high: bool = False
    cmd8_handle_used: bool = False
    cmd8_forced_main_stream: bool = False
    cmd8_full_high_body_length: int = 0
    cmd8_layout: str = ""


@dataclass(frozen=True, slots=True)
class DownloadFullHighProbeResult:
    prepare: base.DownloadPrepareResult
    stream_trace: FullHighProbeTrace

    def __getattr__(self, name: str) -> Any:
        return getattr(self.prepare, name)


_STREAM_STATES: dict[str, FullHighProbeTrace] = {}
_RESULT_TRACE: ContextVar[FullHighProbeTrace | None] = ContextVar(
    "reolink_battery_beta21_full_high_trace", default=None
)


def _new_trace(*, attempted: bool = False) -> FullHighProbeTrace:
    return FullHighProbeTrace(
        attempted=attempted,
        sample_limit_bytes=STREAM_SAMPLE_MAX_BYTES,
        sample_limit_frames=STREAM_SAMPLE_MAX_FRAMES,
        aggregate_limit_bytes=STREAM_SAMPLE_MAX_BYTES,
        keepalive_attempted=False,
        keepalive_count=0,
        keepalive_interval_seconds=0.0,
    )


def _clone_trace(trace: FullHighProbeTrace) -> FullHighProbeTrace:
    return copy.deepcopy(trace)


def stream_probe_state(entry_id: str) -> FullHighProbeTrace:
    return _STREAM_STATES.setdefault(entry_id, _new_trace())


def reset_stream_probe_state(entry_id: str) -> None:
    _STREAM_STATES[entry_id] = _new_trace(attempted=True)


def apply_stream_probe_trace(entry_id: str, trace: FullHighProbeTrace | None) -> None:
    if trace is not None:
        _STREAM_STATES[entry_id] = _clone_trace(trace)


def _time_xml(tag: str, value) -> str:
    return (
        f"<{tag}>"
        f"<year>{value.year}</year><month>{value.month}</month><day>{value.day}</day>"
        f"<hour>{value.hour}</hour><minute>{value.minute}</minute><second>{value.second}</second>"
        f"</{tag}>"
    )


def _full_high_xml(candidate: RecordingCandidate, handle: str) -> bytes:
    """Build the public full-high FileInfo shape without exposing values."""
    file_id = candidate.record_id or candidate.file_name
    name = candidate.display_name or os.path.basename(candidate.file_name.replace("\\", "/"))
    file_type = candidate.file_type or "mp4"
    record_type = candidate.record_type or ""

    fields = [
        f"<Id>{escape(file_id)}</Id>",
        f"<fileName>{escape(file_id)}</fileName>",
    ]
    if name:
        # Public full mode contains both the camera name-as-fileName and name.
        fields.append(f"<fileName>{escape(name)}</fileName>")
        fields.append(f"<name>{escape(name)}</name>")
    fields.append(f"<handle>{escape(handle)}</handle>")
    fields.append("<streamType>mainStream</streamType>")
    if file_type:
        fields.append(f"<fileType>{escape(file_type)}</fileType>")
    if record_type:
        fields.append(f"<recordType>{escape(record_type)}</recordType>")
    fields.append(_time_xml("startTime", candidate.start_time))
    fields.append(_time_xml("endTime", candidate.end_time))

    xml = (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<body>\n<FileInfoList version="1.1"><FileInfo>'
        '<channelId>0</channelId>'
        + "".join(fields)
        + '</FileInfo></FileInfoList>\n</body>'
    )
    return xml.encode("utf-8")


def _extract_handle(data: bytes) -> str | None:
    try:
        root = ET.fromstring(data.decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError):
        return None
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() == "handle":
            value = (node.text or "").strip()
            if value:
                return value
    return None


class _FullHighCmd8Connection(beta20._P2PHeartbeatFullTransferConnection):
    """Beta.20 transport with cmd8 built only after cmd13 returns its handle."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_trace = _new_trace(attempted=True)
        self._candidate: RecordingCandidate | None = None
        self._cmd8_encryptor: Callable[[bytes], bytes] | None = None
        self._reserved_cmd8_msg_num: int | None = None
        self._cmd13_handle: str | None = None

    def prepare_full_high_candidate(
        self,
        encryptor: Callable[[bytes], bytes],
        decryptor,
        candidate: RecordingCandidate,
        cmd8_msg_num: int,
    ) -> None:
        self._stream_decryptor = decryptor
        self._stream_trace = _new_trace(attempted=True)
        self._candidate = candidate
        self._cmd8_encryptor = encryptor
        self._reserved_cmd8_msg_num = cmd8_msg_num
        self._cmd13_handle = None
        self._cmd8_wire = None
        self._cmd8_msg_num = None
        self._aggregate = bytearray()

    def _observe_frame(self, frame: beta17._RawDownloadFrame) -> None:
        super()._observe_frame(frame)
        if frame.cmd_id != 13 or self._cmd13_handle is not None or not frame.body:
            return
        decoded = self._try_aes(frame.body, frame.header)
        if decoded is None:
            return
        self._cmd13_handle = _extract_handle(decoded)

    def _build_full_high_cmd8(self) -> tuple[bytes, int, int]:
        if self._candidate is None or self._cmd8_encryptor is None:
            raise RuntimeError("beta21 candidate/encryptor not prepared")
        if self._reserved_cmd8_msg_num is None:
            raise RuntimeError("beta21 cmd8 msgNum not reserved")
        if not self._cmd13_handle:
            raise RuntimeError("beta21 cmd13 response did not provide a handle")

        payload = _full_high_xml(self._candidate, self._cmd13_handle)
        body = self._cmd8_encryptor(payload)
        msg_num = self._reserved_cmd8_msg_num
        header = (
            beta17.transport_mod.BAICHUAN_MAGIC
            + (8).to_bytes(4, "little")
            + len(body).to_bytes(4, "little")
            + base.DOWNLOAD_HEADER_CHANNEL_ID.to_bytes(1, "little")
            + base.DOWNLOAD_STREAM_TYPE.to_bytes(1, "little")
            + msg_num.to_bytes(2, "little")
            + (0).to_bytes(2, "little")
            + base.FILE_DOWNLOAD_MESSAGE_CLASS.to_bytes(2, "little")
            + (0).to_bytes(4, "little")
        )

        trace = self._stream_trace
        trace.cmd8_full_high = True
        trace.cmd8_handle_used = True
        trace.cmd8_forced_main_stream = True
        trace.cmd8_full_high_body_length = len(body)
        trace.cmd8_layout = "full_high_handle_mainStream"
        trace.cmd8_request_msg_num = msg_num
        trace.cmd8_request_body_length = len(body)
        trace.cmd8_request_payload_offset = 0
        self._cmd8_wire = header + body
        self._cmd8_msg_num = msg_num
        return self._cmd8_wire, msg_num, len(body)

    def _release_collected_media(self) -> None:
        """Release the bounded collector after the active consumer is finished."""
        self._aggregate = bytearray()

    def _publish_result_trace(self) -> None:
        _RESULT_TRACE.set(_clone_trace(self._stream_trace))

    async def send_file_download_probe(
        self,
        wire: bytes,
        *,
        expected_msg_num: int,
        timeout: float = 10.0,
    ) -> beta17.transport_mod.FileDownloadFrameMetadata:
        if not self.connection_open:
            await self.connect()
        protocol = self._protocol
        if not isinstance(protocol, beta20._P2PHeartbeatProbeProtocol):
            raise RuntimeError(  # noqa: TRY004 - preserved download error contract.
                "unexpected beta21 Baichuan UDP protocol"
            )

        trace = self._stream_trace
        first_future = protocol.arm_stream_probe(expected_msg_num, trace, self._observe_frame)
        started_at = self._loop.time()
        reason = ""
        self._apply_p2p_heartbeat_trace(trace, snapshot_pre_cmd13=True)
        try:
            await self._send_reliable_download_packet(
                wire, cmd_id=13, response_future=first_future
            )
            first = await asyncio.wait_for(
                asyncio.shield(first_future), timeout=max(float(timeout), 1.0)
            )
            if first.response_code not in base.ACCEPTED_PREPARE_RESPONSE_CODES:
                reason = f"cmd13_response_{first.response_code}"
                return first

            cmd8_wire, _msg_num, _body_len = self._build_full_high_cmd8()
            trace.cmd8_attempted = True
            cmd8_delivery = protocol.arm_cmd8_delivery_future()
            await self._send_reliable_download_packet(
                cmd8_wire, cmd_id=8, response_future=cmd8_delivery
            )

            while True:
                if trace.expected_size_reached:
                    reason = "expected_size_reached"
                    break
                if trace.aggregate_bytes >= trace.aggregate_limit_bytes:
                    reason = "aggregate_limit"
                    break
                stop_future = protocol._stream_stop_future
                if stop_future is not None and stop_future.done():
                    reason = stop_future.result()
                    if reason in {"byte_limit", "frame_limit"}:
                        reason = "collector_limit"
                    break
                now = self._loop.time()
                if now - started_at >= beta20.STREAM_HARD_TIMEOUT:
                    reason = "hard_timeout"
                    break
                if protocol.stream_idle_expired(now, beta20.STREAM_IDLE_TIMEOUT):
                    reason = "idle_timeout"
                    break
                await asyncio.sleep(0.05)
            return first
        except BaseException:
            if not trace.termination_reason:
                trace.termination_reason = reason or "collector_exception"
            raise
        finally:
            trace.distinct_msg_num_count = len(protocol._stream_msg_nums)
            trace.remote_disconnect_observed = protocol.remote_disconnect_observed
            trace.connection_lost_exception_present = protocol.connection_lost_exception_present
            self._apply_p2p_heartbeat_trace(trace)
            self._apply_udp_reliability_trace(trace, protocol=protocol)
            if not trace.termination_reason:
                trace.termination_reason = reason or "collector_stopped"
            trace.elapsed_seconds = round(self._loop.time() - started_at, 3)
            _RESULT_TRACE.set(_clone_trace(trace))
            protocol.clear_stream_probe()
            self._stream_decryptor = None
            self._candidate = None
            self._cmd8_encryptor = None
            self._reserved_cmd8_msg_num = None
            self._cmd13_handle = None
            self._cmd8_wire = None
            self._cmd8_msg_num = None
            self._release_collected_media()


def _build_cmd13_wire(baichuan: Any, uid: str, candidate: RecordingCandidate):
    """Build proven beta16 cmd13 and reserve a later full-high cmd8 msgNum."""
    wire, request, identity = _beta16_build_cmd13_wire(baichuan, uid, candidate)
    cmd8_msg_num = base._next_download_msg_num(baichuan)
    connection = getattr(baichuan, "_connection", None)
    if isinstance(connection, _FullHighCmd8Connection):
        connection.prepare_full_high_candidate(
            baichuan._aes_encrypt,
            baichuan._aes_decrypt,
            candidate,
            cmd8_msg_num,
        )
    return wire, request, identity


async def async_prepare_download_for_event(*args, **kwargs) -> DownloadFullHighProbeResult:
    token = _RESULT_TRACE.set(None)
    try:
        try:
            prepare = await base.async_prepare_download_for_event(*args, **kwargs)
        except base.CameraStageError as err:
            trace = _RESULT_TRACE.get()
            if trace is not None:
                err.stream_trace = _clone_trace(trace)
            raise
        trace = _RESULT_TRACE.get() or _new_trace(attempted=True)
        if not trace.termination_reason:
            trace.termination_reason = "collector_not_started"
        return DownloadFullHighProbeResult(prepare=prepare, stream_trace=_clone_trace(trace))
    finally:
        _RESULT_TRACE.reset(token)


base.BoundBaichuanUdpConnection = _FullHighCmd8Connection
base._build_cmd13_wire = _build_cmd13_wire

apply_file_info_trace = base.apply_file_info_trace
apply_identity_trace = base.apply_identity_trace
download_prepare_state = base.download_prepare_state
