"""Controlled beta.18 probe: accepted cmd13 prepare followed by cmd8 media request.

The physically accepted beta.16/beta.17 cmd13 request remains unchanged:
class 0x6482, header channel 7, stream 0, msgNum16, Id-only FileInfo,
AES-encrypted XML, no Extension and payload_offset=0.  After the cmd13 XML
response is observed, beta.18 sends cmd8 in the same authenticated camera
session using the exact same encrypted Id-only body and a fresh msgNum16.
Only a bounded media-shape sample is retained in memory; no recording bytes,
UIDs, paths, names, handles or credentials are exposed in diagnostics.
"""

from __future__ import annotations

import asyncio
import copy
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from . import recording_download_probe as base
from . import recording_download_probe_beta17 as beta17
from .recording_download_probe_beta16 import _build_cmd13_wire as _beta16_build_cmd13_wire
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = "cmd13_prepare_then_cmd8_id_only_no_extension_media_shape"
CMD8_ID = 8
STREAM_IDLE_TIMEOUT = 8.0
STREAM_HARD_TIMEOUT = 90.0
STREAM_SAMPLE_MAX_BYTES = 2 * 1024 * 1024
STREAM_SAMPLE_MAX_FRAMES = 512


@dataclass(slots=True)
class Cmd8ProbeTrace(beta17.StreamProbeTrace):
    """Secret-safe telemetry for the same-session cmd13 -> cmd8 probe."""

    cmd8_attempted: bool = False
    cmd8_request_msg_num: int | None = None
    cmd8_request_body_length: int | None = None
    cmd8_request_payload_offset: int | None = None
    cmd8_first_response_code: int | None = None
    cmd8_first_body_length: int | None = None
    cmd8_first_kind: str = ""
    cmd8_xml_frames: int = 0
    cmd8_media_frames: int = 0
    cmd8_unknown_frames: int = 0
    cmd8_media_observed: bool = False


@dataclass(frozen=True, slots=True)
class DownloadCmd8ProbeResult:
    """Base prepare result plus bounded cmd8 stream telemetry."""

    prepare: base.DownloadPrepareResult
    stream_trace: Cmd8ProbeTrace

    def __getattr__(self, name: str) -> Any:
        return getattr(self.prepare, name)


_STREAM_STATES: dict[str, Cmd8ProbeTrace] = {}
_RESULT_TRACE: ContextVar[Cmd8ProbeTrace | None] = ContextVar(
    "reolink_battery_beta18_cmd8_trace", default=None
)


def _clone_trace(trace: Cmd8ProbeTrace) -> Cmd8ProbeTrace:
    return copy.deepcopy(trace)


def _new_trace(*, attempted: bool = False) -> Cmd8ProbeTrace:
    return Cmd8ProbeTrace(
        attempted=attempted,
        sample_limit_bytes=STREAM_SAMPLE_MAX_BYTES,
        sample_limit_frames=STREAM_SAMPLE_MAX_FRAMES,
    )


def stream_probe_state(entry_id: str) -> Cmd8ProbeTrace:
    """Return persistent secret-safe beta.18 telemetry for diagnostics."""
    return _STREAM_STATES.setdefault(entry_id, _new_trace())


def reset_stream_probe_state(entry_id: str) -> None:
    """Reset diagnostics before one explicit manual attempt."""
    _STREAM_STATES[entry_id] = _new_trace(attempted=True)


def apply_stream_probe_trace(entry_id: str, trace: Cmd8ProbeTrace | None) -> None:
    """Persist only secret-safe cmd8 telemetry for diagnostics."""
    if trace is not None:
        _STREAM_STATES[entry_id] = _clone_trace(trace)


def _build_cmd8_wire_from_cmd13(
    baichuan: Any,
    cmd13_wire: bytes,
) -> tuple[bytes, int, int, int]:
    """Reuse the accepted encrypted Id-only body with cmd8 and a fresh msgNum16."""
    if len(cmd13_wire) < 24:
        raise ValueError("cmd13 wire is shorter than the modern 24-byte header")
    body_length = int.from_bytes(cmd13_wire[8:12], "little")
    if len(cmd13_wire) != 24 + body_length:
        raise ValueError("cmd13 wire length does not match its body length")
    if int.from_bytes(cmd13_wire[18:20], "little") != base.FILE_DOWNLOAD_MESSAGE_CLASS:
        raise ValueError("cmd13 wire does not use FILE_DOWNLOAD class")

    msg_num = base._next_download_msg_num(baichuan)
    cmd8 = bytearray(cmd13_wire)
    cmd8[4:8] = CMD8_ID.to_bytes(4, "little")
    cmd8[14:16] = msg_num.to_bytes(2, "little")
    cmd8[16:18] = (0).to_bytes(2, "little")
    payload_offset = int.from_bytes(cmd8[20:24], "little")
    return bytes(cmd8), msg_num, body_length, payload_offset


class _Cmd8ProbeConnection(beta17._StreamProbeConnection):
    """Same bound UDP session, with cmd8 sent after the accepted cmd13 XML."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_trace = _new_trace(attempted=True)
        self._cmd8_wire: bytes | None = None
        self._cmd8_msg_num: int | None = None

    def prepare_cmd8_probe(
        self,
        decryptor,
        cmd8_wire: bytes,
        cmd8_msg_num: int,
        cmd8_body_length: int,
        cmd8_payload_offset: int,
    ) -> None:
        """Attach decryptor and the prebuilt cmd8 frame without exposing identity."""
        self._stream_decryptor = decryptor
        self._stream_trace = _new_trace(attempted=True)
        self._stream_trace.cmd8_request_msg_num = cmd8_msg_num
        self._stream_trace.cmd8_request_body_length = cmd8_body_length
        self._stream_trace.cmd8_request_payload_offset = cmd8_payload_offset
        self._cmd8_wire = cmd8_wire
        self._cmd8_msg_num = cmd8_msg_num

    def _observe_frame(self, frame: beta17._RawDownloadFrame) -> None:
        trace = self._stream_trace
        before_xml = trace.xml_frames
        before_media = trace.media_frames
        before_unknown = trace.unknown_frames
        before_mp4 = trace.mp4_frames
        before_bcmedia = trace.bcmedia_frames

        super()._observe_frame(frame)

        if frame.cmd_id != CMD8_ID:
            return
        if trace.cmd8_first_response_code is None:
            trace.cmd8_first_response_code = frame.response_code
            trace.cmd8_first_body_length = frame.body_length

        xml_delta = trace.xml_frames - before_xml
        media_delta = trace.media_frames - before_media
        unknown_delta = trace.unknown_frames - before_unknown
        if xml_delta > 0:
            trace.cmd8_xml_frames += xml_delta
            if not trace.cmd8_first_kind:
                trace.cmd8_first_kind = "xml"
        if media_delta > 0:
            trace.cmd8_media_frames += media_delta
            trace.cmd8_media_observed = True
            if not trace.cmd8_first_kind:
                if trace.mp4_frames > before_mp4:
                    trace.cmd8_first_kind = "mp4"
                elif trace.bcmedia_frames > before_bcmedia:
                    trace.cmd8_first_kind = "bcmedia"
                else:
                    trace.cmd8_first_kind = "media"
        if unknown_delta > 0:
            trace.cmd8_unknown_frames += unknown_delta
            if not trace.cmd8_first_kind:
                trace.cmd8_first_kind = "unknown"

    async def send_file_download_probe(
        self,
        wire: bytes,
        *,
        expected_msg_num: int,
        timeout: float = 10.0,
    ) -> beta17.transport_mod.FileDownloadFrameMetadata:
        """Send accepted cmd13, then cmd8, and sample both in the same session."""
        if not self.connection_open:
            await self.connect()
        protocol = self._protocol
        if not isinstance(protocol, beta17._StreamProbeProtocol):
            raise RuntimeError("unexpected beta18 Baichuan UDP protocol")
        if self._cmd8_wire is None or self._cmd8_msg_num is None:
            raise RuntimeError("cmd8 probe was not prepared")

        trace = self._stream_trace
        first_future = protocol.arm_stream_probe(
            expected_msg_num,
            trace,
            self._observe_frame,
        )
        started_at = self._loop.time()
        reason = ""
        try:
            await self.send_without_wait(wire, cmd_id=13, timeout=min(timeout, 5.0))
            first = await asyncio.wait_for(
                asyncio.shield(first_future), timeout=max(float(timeout), 1.0)
            )

            if first.response_code not in base.ACCEPTED_PREPARE_RESPONSE_CODES:
                reason = f"cmd13_response_{first.response_code}"
                return first

            trace.cmd8_attempted = True
            await self.send_without_wait(
                self._cmd8_wire,
                cmd_id=CMD8_ID,
                timeout=min(max(float(timeout), 1.0), 5.0),
            )

            while True:
                stop_future = protocol._stream_stop_future
                if stop_future is not None and stop_future.done():
                    reason = stop_future.result()
                    break
                now = self._loop.time()
                if now - started_at >= STREAM_HARD_TIMEOUT:
                    reason = "hard_timeout"
                    break
                last_frame_at = protocol._stream_last_frame_at
                if last_frame_at and now - last_frame_at >= STREAM_IDLE_TIMEOUT:
                    reason = "idle_timeout"
                    break
                await asyncio.sleep(0.1)

            return first
        except BaseException:
            if not trace.termination_reason:
                trace.termination_reason = reason or "collector_exception"
            raise
        finally:
            trace.distinct_msg_num_count = len(protocol._stream_msg_nums)
            if not trace.termination_reason:
                trace.termination_reason = reason or "collector_stopped"
            trace.elapsed_seconds = round(self._loop.time() - started_at, 3)
            _RESULT_TRACE.set(_clone_trace(trace))
            protocol.clear_stream_probe()
            self._stream_decryptor = None
            self._cmd8_wire = None
            self._cmd8_msg_num = None


def _build_cmd13_wire(
    baichuan: Any,
    uid: str,
    candidate: RecordingCandidate,
):
    """Build proven cmd13 and prepare matching cmd8 before the same-session send."""
    wire, request, identity = _beta16_build_cmd13_wire(baichuan, uid, candidate)
    cmd8_wire, cmd8_msg_num, body_length, payload_offset = _build_cmd8_wire_from_cmd13(
        baichuan, wire
    )
    connection = getattr(baichuan, "_connection", None)
    if isinstance(connection, _Cmd8ProbeConnection):
        connection.prepare_cmd8_probe(
            baichuan._aes_decrypt,
            cmd8_wire,
            cmd8_msg_num,
            body_length,
            payload_offset,
        )
    return wire, request, identity


async def async_prepare_download_for_event(*args, **kwargs) -> DownloadCmd8ProbeResult:
    """Run proven cmd13 prepare, then bounded cmd8 media-shape probe."""
    token = _RESULT_TRACE.set(None)
    try:
        try:
            prepare = await base.async_prepare_download_for_event(*args, **kwargs)
        except base.CameraStageError as err:
            trace = _RESULT_TRACE.get()
            if trace is not None:
                setattr(err, "stream_trace", _clone_trace(trace))
            raise
        trace = _RESULT_TRACE.get() or _new_trace(attempted=True)
        if not trace.termination_reason:
            trace.termination_reason = "collector_not_started"
        return DownloadCmd8ProbeResult(
            prepare=prepare,
            stream_trace=_clone_trace(trace),
        )
    finally:
        _RESULT_TRACE.reset(token)


# Importing beta17 installs its stream-aware protocol. Replace only the concrete
# connection and frame builder for beta18; UID resolve, wake, login, FileInfo,
# candidate matching and finally-close remain in the proven base coroutine.
base.BoundBaichuanUdpConnection = _Cmd8ProbeConnection
base._build_cmd13_wire = _build_cmd13_wire

apply_file_info_trace = base.apply_file_info_trace
apply_identity_trace = base.apply_identity_trace
download_prepare_state = base.download_prepare_state
