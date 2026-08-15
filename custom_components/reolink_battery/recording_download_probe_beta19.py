"""Beta.19 full-transfer diagnostic: proven cmd13/cmd8 plus UDP keepalive.

This keeps the physically validated beta.18 request sequence unchanged and adds
only the download keepalive cadence used by independent Baichuan implementations.
The cmd8 response bodies are decoded and assembled in memory only, up to a
bounded 16 MiB diagnostic ceiling. No recording file is written and no cmd9 is
sent in this beta.
"""

from __future__ import annotations

import asyncio
import copy
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from . import recording_download_probe as base
from . import recording_download_probe_beta17 as beta17
from . import recording_download_probe_beta18 as beta18
from .recording_download_probe_beta16 import _build_cmd13_wire as _beta16_build_cmd13_wire
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = "cmd13_prepare_cmd8_keepalive_full_transfer_shape"
CMD8_ID = 8
UDP_KEEPALIVE_ID = 234
KEEPALIVE_INTERVAL = 0.75
STREAM_IDLE_TIMEOUT = 25.0
STREAM_HARD_TIMEOUT = 180.0
STREAM_SAMPLE_MAX_BYTES = 16 * 1024 * 1024
STREAM_SAMPLE_MAX_FRAMES = 4096


@dataclass(slots=True)
class FullTransferProbeTrace(beta18.Cmd8ProbeTrace):
    """Secret-safe telemetry for one bounded full-transfer diagnostic."""

    keepalive_attempted: bool = False
    keepalive_count: int = 0
    keepalive_interval_seconds: float = KEEPALIVE_INTERVAL
    decoded_cmd8_chunks: int = 0
    decoded_cmd8_bytes: int = 0
    raw_cmd8_chunks: int = 0
    nonzero_payload_offset_frames: int = 0
    aggregate_bytes: int = 0
    aggregate_limit_bytes: int = STREAM_SAMPLE_MAX_BYTES
    mp4_offset_found: bool = False
    mp4_offset: int | None = None
    mp4_bytes_collected: int = 0
    expected_size_reached: bool = False
    expected_size_match: bool = False
    bcmedia_prefix_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class DownloadFullTransferProbeResult:
    """Base prepare result plus bounded full-transfer telemetry."""

    prepare: base.DownloadPrepareResult
    stream_trace: FullTransferProbeTrace

    def __getattr__(self, name: str) -> Any:
        return getattr(self.prepare, name)


_STREAM_STATES: dict[str, FullTransferProbeTrace] = {}
_RESULT_TRACE: ContextVar[FullTransferProbeTrace | None] = ContextVar(
    "reolink_battery_beta19_full_transfer_trace", default=None
)


def _new_trace(*, attempted: bool = False) -> FullTransferProbeTrace:
    return FullTransferProbeTrace(
        attempted=attempted,
        sample_limit_bytes=STREAM_SAMPLE_MAX_BYTES,
        sample_limit_frames=STREAM_SAMPLE_MAX_FRAMES,
        aggregate_limit_bytes=STREAM_SAMPLE_MAX_BYTES,
    )


def _clone_trace(trace: FullTransferProbeTrace) -> FullTransferProbeTrace:
    return copy.deepcopy(trace)


def stream_probe_state(entry_id: str) -> FullTransferProbeTrace:
    """Return persistent secret-safe beta.19 telemetry."""
    return _STREAM_STATES.setdefault(entry_id, _new_trace())


def reset_stream_probe_state(entry_id: str) -> None:
    """Reset beta.19 diagnostics before one explicit manual attempt."""
    _STREAM_STATES[entry_id] = _new_trace(attempted=True)


def apply_stream_probe_trace(
    entry_id: str, trace: FullTransferProbeTrace | None
) -> None:
    """Persist only secret-safe beta.19 telemetry."""
    if trace is not None:
        _STREAM_STATES[entry_id] = _clone_trace(trace)


def _build_keepalive_wire() -> bytes:
    """Build cmd234, channel 0, stream 0, msgNum 0, modern 24-byte header."""
    return (
        transport_magic()
        + UDP_KEEPALIVE_ID.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + bytes((0, 0))
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0x6414).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
    )


def transport_magic() -> bytes:
    """Return the existing integration Baichuan wire magic."""
    return beta17.transport_mod.BAICHUAN_MAGIC


def _find_mp4_offset(data: bytes | bytearray) -> int | None:
    """Return an embedded ISO-BMFF ftyp box offset when one is structurally sane."""
    marker = bytes(data).find(b"ftyp")
    if marker < 4:
        return None
    start = marker - 4
    size = int.from_bytes(data[start:marker], "big")
    if size == 1:
        return start
    return start if size >= 8 else None


class _FullTransferProbeConnection(beta18._Cmd8ProbeConnection):
    """Same authenticated session, with keepalive and bounded chunk assembly."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_trace = _new_trace(attempted=True)
        self._aggregate = bytearray()

    def prepare_cmd8_probe(
        self,
        decryptor,
        cmd8_wire: bytes,
        cmd8_msg_num: int,
        cmd8_body_length: int,
        cmd8_payload_offset: int,
    ) -> None:
        """Prepare beta.19 while preserving the proven beta.18 cmd8 bytes."""
        self._stream_decryptor = decryptor
        self._stream_trace = _new_trace(attempted=True)
        self._stream_trace.cmd8_request_msg_num = cmd8_msg_num
        self._stream_trace.cmd8_request_body_length = cmd8_body_length
        self._stream_trace.cmd8_request_payload_offset = cmd8_payload_offset
        self._cmd8_wire = cmd8_wire
        self._cmd8_msg_num = cmd8_msg_num
        self._aggregate = bytearray()

    def _decoded_cmd8_body(self, frame: beta17._RawDownloadFrame) -> tuple[bytes, bool]:
        """Decode one cmd8 body using the same framing rules used by beta.17."""
        if not frame.body:
            return b"", False
        if frame.payload_offset <= 0:
            decoded = self._try_aes(frame.body, frame.header)
            if decoded is not None:
                return decoded, True
            return frame.body, False

        self._stream_trace.nonzero_payload_offset_frames += 1
        enc_extension = frame.body[: frame.payload_offset]
        payload = frame.body[frame.payload_offset :]
        extension = self._try_aes(enc_extension, frame.header) or b""
        if b"<binaryData>1</binaryData>" in extension and payload:
            encrypt_len = beta17._extension_encrypt_len(extension)
            if encrypt_len is not None and encrypt_len > 0:
                encrypt_len = min(encrypt_len, len(payload))
                prefix = self._try_aes(payload[:encrypt_len], frame.header)
                if prefix is not None:
                    return prefix + payload[encrypt_len:], True
        decoded = self._try_aes(payload, frame.header)
        if decoded is not None:
            return decoded, True
        return payload, False

    def _observe_frame(self, frame: beta17._RawDownloadFrame) -> None:
        super()._observe_frame(frame)
        if frame.cmd_id != CMD8_ID or frame.response_code not in (0, 200):
            return

        chunk, decoded = self._decoded_cmd8_body(frame)
        if not chunk or beta17._looks_like_xml(chunk):
            return

        trace = self._stream_trace
        if decoded:
            trace.decoded_cmd8_chunks += 1
            trace.decoded_cmd8_bytes += len(chunk)
        else:
            trace.raw_cmd8_chunks += 1

        remaining = max(trace.aggregate_limit_bytes - len(self._aggregate), 0)
        if remaining:
            self._aggregate.extend(chunk[:remaining])
        trace.aggregate_bytes = len(self._aggregate)

        if trace.mp4_offset is None:
            offset = _find_mp4_offset(self._aggregate)
            if offset is not None:
                trace.mp4_offset = offset
                trace.mp4_offset_found = True
                trace.bcmedia_prefix_bytes = offset

        if trace.mp4_offset is not None:
            trace.mp4_bytes_collected = max(
                len(self._aggregate) - trace.mp4_offset,
                0,
            )
            expected = trace.xml_reported_size
            if expected is not None and expected > 0:
                trace.expected_size_reached = trace.mp4_bytes_collected >= expected
                trace.expected_size_match = trace.mp4_bytes_collected == expected

    async def send_file_download_probe(
        self,
        wire: bytes,
        *,
        expected_msg_num: int,
        timeout: float = 10.0,
    ) -> beta17.transport_mod.FileDownloadFrameMetadata:
        """Send cmd13 -> cmd8, keep the UDP session alive, and assemble a bounded sample."""
        if not self.connection_open:
            await self.connect()
        protocol = self._protocol
        if not isinstance(protocol, beta17._StreamProbeProtocol):
            raise RuntimeError("unexpected beta19 Baichuan UDP protocol")
        if self._cmd8_wire is None or self._cmd8_msg_num is None:
            raise RuntimeError("cmd8 probe was not prepared")

        trace = self._stream_trace
        first_future = protocol.arm_stream_probe(
            expected_msg_num,
            trace,
            self._observe_frame,
        )
        started_at = self._loop.time()
        next_keepalive_at = started_at
        reason = ""
        keepalive_wire = _build_keepalive_wire()
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
                now = self._loop.time()
                if now >= next_keepalive_at:
                    trace.keepalive_attempted = True
                    await self.send_without_wait(
                        keepalive_wire,
                        cmd_id=UDP_KEEPALIVE_ID,
                        timeout=5.0,
                    )
                    trace.keepalive_count += 1
                    next_keepalive_at = now + KEEPALIVE_INTERVAL

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
                if now - started_at >= STREAM_HARD_TIMEOUT:
                    reason = "hard_timeout"
                    break
                last_frame_at = protocol._stream_last_frame_at
                if last_frame_at and now - last_frame_at >= STREAM_IDLE_TIMEOUT:
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
            if not trace.termination_reason:
                trace.termination_reason = reason or "collector_stopped"
            trace.elapsed_seconds = round(self._loop.time() - started_at, 3)
            _RESULT_TRACE.set(_clone_trace(trace))
            protocol.clear_stream_probe()
            self._stream_decryptor = None
            self._cmd8_wire = None
            self._cmd8_msg_num = None
            self._aggregate = bytearray()


def _build_cmd13_wire(
    baichuan: Any,
    uid: str,
    candidate: RecordingCandidate,
):
    """Build the proven cmd13 and exact beta.18 cmd8 before opening the stream."""
    wire, request, identity = _beta16_build_cmd13_wire(baichuan, uid, candidate)
    cmd8_wire, cmd8_msg_num, body_length, payload_offset = (
        beta18._build_cmd8_wire_from_cmd13(baichuan, wire)
    )
    connection = getattr(baichuan, "_connection", None)
    if isinstance(connection, _FullTransferProbeConnection):
        connection.prepare_cmd8_probe(
            baichuan._aes_decrypt,
            cmd8_wire,
            cmd8_msg_num,
            body_length,
            payload_offset,
        )
    return wire, request, identity


async def async_prepare_download_for_event(*args, **kwargs) -> DownloadFullTransferProbeResult:
    """Run the proven same-session sequence with keepalive and bounded assembly."""
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
        return DownloadFullTransferProbeResult(
            prepare=prepare,
            stream_trace=_clone_trace(trace),
        )
    finally:
        _RESULT_TRACE.reset(token)


# Beta.18 import installs its cmd8-aware extension points. Replace only the
# concrete connection and frame builder for beta.19. UID discovery, login,
# FileInfo search, candidate matching and finally-close remain unchanged.
base.BoundBaichuanUdpConnection = _FullTransferProbeConnection
base._build_cmd13_wire = _build_cmd13_wire

apply_file_info_trace = base.apply_file_info_trace
apply_identity_trace = base.apply_identity_trace
download_prepare_state = base.download_prepare_state
