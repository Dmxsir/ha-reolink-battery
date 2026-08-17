"""Controlled beta.17 probe for the accepted cmd13 download stream shape.

The successful beta.16 request is kept byte-for-byte: cmd13, class 0x6482,
header channel 7, stream 0, msgNum16, Id-only FileInfo body, AES-encrypted XML,
no Extension and payload_offset=0.  This module only keeps the same session
open briefly after the accepted response and samples continuation frames.  It
never exposes recording identities or raw payload bytes in diagnostics.
"""

from __future__ import annotations

import asyncio
import copy
import socket
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable
from xml.etree import ElementTree as ET

from . import recording_download_probe as base
from . import transport as transport_mod
from .recording_download_probe_beta16 import _build_cmd13_wire as _beta16_build_cmd13_wire
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = "fileinfo_id_only_no_extension_stream_shape"
STREAM_IDLE_TIMEOUT = 8.0
STREAM_HARD_TIMEOUT = 60.0
STREAM_SAMPLE_MAX_BYTES = 2 * 1024 * 1024
STREAM_SAMPLE_MAX_FRAMES = 256


@dataclass(slots=True)
class StreamProbeTrace:
    """Secret-safe stream-shape telemetry for one explicit manual probe."""

    attempted: bool = False
    frame_count: int = 0
    total_body_bytes: int = 0
    cmd13_frames: int = 0
    cmd8_frames: int = 0
    distinct_msg_num_count: int = 0
    max_body_length: int = 0
    response_codes: list[int] = field(default_factory=list)
    completion_code: int | None = None
    termination_reason: str = ""
    elapsed_seconds: float | None = None
    first_frame_kind: str = ""
    xml_frames: int = 0
    media_frames: int = 0
    mp4_frames: int = 0
    bcmedia_frames: int = 0
    unknown_frames: int = 0
    aes_decrypted_frames: int = 0
    media_bytes_observed: int = 0
    xml_reported_size: int | None = None
    xml_file_info_present: bool = False
    xml_handle_present: bool = False
    xml_binary_data_present: bool = False
    sample_limit_bytes: int = STREAM_SAMPLE_MAX_BYTES
    sample_limit_frames: int = STREAM_SAMPLE_MAX_FRAMES
    raw_values_exposed: bool = False


@dataclass(frozen=True, slots=True)
class DownloadStreamProbeResult:
    """Base prepare result plus secret-safe continuation telemetry."""

    prepare: base.DownloadPrepareResult
    stream_trace: StreamProbeTrace

    def __getattr__(self, name: str) -> Any:
        return getattr(self.prepare, name)


@dataclass(frozen=True, slots=True)
class _RawDownloadFrame:
    cmd_id: int
    response_code: int
    message_class: int
    header_channel_id: int
    stream_type: int
    msg_num: int
    body_length: int
    payload_offset: int
    header: bytes
    body: bytes


_STREAM_STATES: dict[str, StreamProbeTrace] = {}
_RESULT_TRACE: ContextVar[StreamProbeTrace | None] = ContextVar(
    "reolink_battery_beta17_stream_trace", default=None
)


def _clone_trace(trace: StreamProbeTrace) -> StreamProbeTrace:
    return copy.deepcopy(trace)


def stream_probe_state(entry_id: str) -> StreamProbeTrace:
    """Return persistent secret-safe beta.17 telemetry for diagnostics."""
    return _STREAM_STATES.setdefault(entry_id, StreamProbeTrace())


def reset_stream_probe_state(entry_id: str) -> None:
    """Reset diagnostics before one explicit manual attempt."""
    _STREAM_STATES[entry_id] = StreamProbeTrace(attempted=True)


def apply_stream_probe_trace(entry_id: str, trace: StreamProbeTrace | None) -> None:
    """Persist only secret-safe stream telemetry for diagnostics."""
    if trace is not None:
        _STREAM_STATES[entry_id] = _clone_trace(trace)


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _int_text(value: str | None) -> int | None:
    try:
        return int(value.strip()) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _inspect_xml(data: bytes, trace: StreamProbeTrace) -> None:
    """Extract only non-identifying shape/size facts from decrypted XML."""
    try:
        root = ET.fromstring(data.decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError):
        return

    size_low: int | None = None
    size_high: int | None = None
    direct_sizes: list[int] = []
    for node in root.iter():
        tag = _local_tag(node.tag)
        text = (node.text or "").strip()
        if tag == "fileinfo":
            trace.xml_file_info_present = True
        elif tag == "handle" and text:
            trace.xml_handle_present = True
        elif tag == "binarydata" and text == "1":
            trace.xml_binary_data_present = True
        elif tag in {"size", "filesize", "length"}:
            value = _int_text(text)
            if value is not None and value >= 0:
                direct_sizes.append(value)
        elif tag == "sizel":
            size_low = _int_text(text)
        elif tag == "sizeh":
            size_high = _int_text(text)

    combined_size: int | None = None
    if size_low is not None or size_high is not None:
        combined_size = (size_low or 0) + ((size_high or 0) << 32)
    candidates = [value for value in direct_sizes if value > 0]
    if combined_size is not None and combined_size > 0:
        candidates.append(combined_size)
    if candidates:
        found = max(candidates)
        trace.xml_reported_size = max(trace.xml_reported_size or 0, found)


def _looks_like_xml(data: bytes) -> bool:
    stripped = data.lstrip()
    return stripped.startswith(
        (b"<?xml", b"<body", b"<FileInfo", b"<Extension")
    )


def _looks_like_mp4(data: bytes) -> bool:
    marker = data.find(b"ftyp")
    if marker < 4:
        return False
    size = int.from_bytes(data[marker - 4 : marker], "big")
    return size == 1 or size >= 8


def _looks_like_bcmedia(data: bytes) -> bool:
    return data.startswith((b"1001", b"1002"))


def _extension_encrypt_len(extension: bytes) -> int | None:
    if not _looks_like_xml(extension):
        return None
    try:
        root = ET.fromstring(extension.decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError):
        return None
    for node in root.iter():
        if _local_tag(node.tag) == "encryptlen":
            value = _int_text(node.text)
            return value if value is not None and value >= 0 else None
    return None


class _StreamProbeProtocol(transport_mod._IdempotentUdpClientProtocol):
    """Observe continuation frames before reolink-aio parses/drops them."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_expected_msg_num: int | None = None
        self._stream_started = False
        self._stream_trace: StreamProbeTrace | None = None
        self._stream_observer: Callable[[_RawDownloadFrame], None] | None = None
        self._stream_first_future: asyncio.Future[
            transport_mod.FileDownloadFrameMetadata
        ] | None = None
        self._stream_stop_future: asyncio.Future[str] | None = None
        self._stream_last_frame_at: float = 0.0
        self._stream_msg_nums: set[int] = set()

    def arm_stream_probe(
        self,
        expected_msg_num: int,
        trace: StreamProbeTrace,
        observer: Callable[[_RawDownloadFrame], None],
    ) -> asyncio.Future[transport_mod.FileDownloadFrameMetadata]:
        if self._stream_first_future is not None and not self._stream_first_future.done():
            raise RuntimeError("file download stream probe already armed")
        self._stream_expected_msg_num = expected_msg_num
        self._stream_started = False
        self._stream_trace = trace
        self._stream_observer = observer
        self._stream_first_future = self._loop.create_future()
        self._stream_stop_future = self._loop.create_future()
        self._stream_last_frame_at = 0.0
        self._stream_msg_nums = set()
        return self._stream_first_future

    def clear_stream_probe(self) -> None:
        self._stream_expected_msg_num = None
        self._stream_started = False
        self._stream_trace = None
        self._stream_observer = None
        self._stream_first_future = None
        self._stream_stop_future = None
        self._stream_last_frame_at = 0.0
        self._stream_msg_nums = set()

    def _stop_stream_probe(self, reason: str) -> None:
        future = self._stream_stop_future
        if future is not None and not future.done():
            future.set_result(reason)

    def _observe_stream_frame(self, raw: bytes) -> None:
        trace = self._stream_trace
        expected = self._stream_expected_msg_num
        if trace is None or expected is None or len(raw) < 20:
            return

        cmd_id = int.from_bytes(raw[4:8], "little")
        if cmd_id not in (8, 13):
            return
        body_length = int.from_bytes(raw[8:12], "little")
        header_channel_id = raw[12]
        stream_type = raw[13]
        msg_num = int.from_bytes(raw[14:16], "little")
        response_code = int.from_bytes(raw[16:18], "little")
        message_class = int.from_bytes(raw[18:20], "little")
        header_length = 24 if message_class in (0x0000, 0x6414, 0x6482) else 20
        if len(raw) < header_length + body_length:
            return

        if not self._stream_started:
            if cmd_id != 13 or msg_num != expected:
                return
            self._stream_started = True
        elif cmd_id not in (8, 13):
            return

        payload_offset = (
            int.from_bytes(raw[20:24], "little") if header_length == 24 else 0
        )
        if payload_offset > body_length:
            payload_offset = body_length
        body = raw[header_length : header_length + body_length]
        frame = _RawDownloadFrame(
            cmd_id=cmd_id,
            response_code=response_code,
            message_class=message_class,
            header_channel_id=header_channel_id,
            stream_type=stream_type,
            msg_num=msg_num,
            body_length=body_length,
            payload_offset=payload_offset,
            header=raw[:header_length],
            body=body,
        )

        trace.frame_count += 1
        trace.total_body_bytes += body_length
        trace.max_body_length = max(trace.max_body_length, body_length)
        if cmd_id == 13:
            trace.cmd13_frames += 1
        else:
            trace.cmd8_frames += 1
        if response_code not in trace.response_codes:
            trace.response_codes.append(response_code)
        self._stream_msg_nums.add(msg_num)
        self._stream_last_frame_at = self._loop.time()

        observer = self._stream_observer
        if observer is not None:
            try:
                observer(frame)
            except Exception:
                trace.unknown_frames += 1

        first = self._stream_first_future
        if first is not None and not first.done():
            first.set_result(
                transport_mod.FileDownloadFrameMetadata(
                    response_code=response_code,
                    message_class=message_class,
                    header_channel_id=header_channel_id,
                    stream_type=stream_type,
                    msg_num=msg_num,
                    body_length=body_length,
                    payload_offset=payload_offset,
                    payload_length=max(0, body_length - payload_offset),
                )
            )

        if response_code in (201, 300):
            trace.completion_code = response_code
            self._stop_stream_probe("completion_code")
        elif response_code not in (0, 200):
            self._stop_stream_probe(f"response_{response_code}")
        elif trace.total_body_bytes >= trace.sample_limit_bytes:
            self._stop_stream_probe("byte_limit")
        elif trace.frame_count >= trace.sample_limit_frames:
            self._stop_stream_probe("frame_limit")

    def parse_bc_data(self) -> None:
        self._observe_stream_frame(self._data)
        super().parse_bc_data()

    def connection_lost(self, exc: Exception | None = None) -> None:
        first = self._stream_first_future
        if first is not None and not first.done():
            first.set_exception(exc or ConnectionError("Baichuan connection closed"))
        self._stop_stream_probe("connection_closed")
        super().connection_lost(exc)


class _StreamProbeConnection(transport_mod.BoundBaichuanUdpConnection):
    """Bound UDP transport that samples continuation frames after beta.16 cmd13."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_decryptor: Callable[..., bytes | str] | None = None
        self._stream_trace = StreamProbeTrace(attempted=True)

    def prepare_stream_probe(self, decryptor: Callable[..., bytes | str]) -> None:
        self._stream_decryptor = decryptor
        self._stream_trace = StreamProbeTrace(attempted=True)

    async def _create_connection(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.source_ip, 0))
            sock.setblocking(False)
            created = await self._loop.create_datagram_endpoint(
                lambda: _StreamProbeProtocol(
                    self._loop,
                    self._host,
                    self.drop_connection(),
                    self.cancel_ack_timeout,
                    self._push_callback,
                    self._close_callback,
                ),
                sock=sock,
            )
        except BaseException:
            sock.close()
            raise
        transport, protocol = created
        _, self._local_port = transport.get_extra_info("sockname")
        return transport, protocol

    def _try_aes(self, data: bytes, header: bytes) -> bytes | None:
        if not data or self._stream_decryptor is None:
            return None
        try:
            decoded = self._stream_decryptor(data, header, decode=False)
        except Exception:
            return None
        return decoded if isinstance(decoded, bytes) else None

    def _classify_candidate(
        self,
        label: str,
        data: bytes,
        decrypted: bool,
    ) -> bool:
        trace = self._stream_trace
        if _looks_like_xml(data):
            trace.xml_frames += 1
            if decrypted:
                trace.aes_decrypted_frames += 1
            _inspect_xml(data, trace)
            if not trace.first_frame_kind:
                trace.first_frame_kind = f"xml_{label}"
            return True
        if _looks_like_mp4(data):
            trace.media_frames += 1
            trace.mp4_frames += 1
            trace.media_bytes_observed += len(data)
            if decrypted:
                trace.aes_decrypted_frames += 1
            if not trace.first_frame_kind:
                trace.first_frame_kind = f"mp4_{label}"
            return True
        if _looks_like_bcmedia(data):
            trace.media_frames += 1
            trace.bcmedia_frames += 1
            trace.media_bytes_observed += len(data)
            if decrypted:
                trace.aes_decrypted_frames += 1
            if not trace.first_frame_kind:
                trace.first_frame_kind = f"bcmedia_{label}"
            return True
        return False

    def _observe_frame(self, frame: _RawDownloadFrame) -> None:
        body = frame.body
        payload_offset = frame.payload_offset
        candidates: list[tuple[str, bytes, bool]] = []

        if payload_offset > 0:
            enc_extension = body[:payload_offset]
            payload = body[payload_offset:]
            extension = self._try_aes(enc_extension, frame.header) or b""
            binary = b"<binaryData>1</binaryData>" in extension
            if binary and payload:
                encrypt_len = _extension_encrypt_len(extension)
                if encrypt_len is not None and encrypt_len > 0:
                    encrypt_len = min(encrypt_len, len(payload))
                    prefix = self._try_aes(payload[:encrypt_len], frame.header)
                    if prefix is not None:
                        candidates.append(
                            ("mixed", prefix + payload[encrypt_len:], True)
                        )
                candidates.append(("raw", payload, False))
                decrypted_payload = self._try_aes(payload, frame.header)
                if decrypted_payload is not None:
                    candidates.append(("aes", decrypted_payload, True))
            else:
                decrypted_payload = self._try_aes(payload, frame.header)
                if decrypted_payload is not None:
                    candidates.append(("aes", decrypted_payload, True))
                candidates.append(("raw", payload, False))
        else:
            decrypted_body = self._try_aes(body, frame.header)
            if decrypted_body is not None:
                candidates.append(("aes", decrypted_body, True))
            candidates.append(("raw", body, False))

        for label, data, decrypted in candidates:
            if data and self._classify_candidate(label, data, decrypted):
                return

        self._stream_trace.unknown_frames += 1
        if not self._stream_trace.first_frame_kind:
            self._stream_trace.first_frame_kind = "unknown"

    async def send_file_download_probe(
        self,
        wire: bytes,
        *,
        expected_msg_num: int,
        timeout: float = 10.0,
    ) -> transport_mod.FileDownloadFrameMetadata:
        if not self.connection_open:
            await self.connect()
        protocol = self._protocol
        if not isinstance(protocol, _StreamProbeProtocol):
            raise RuntimeError("unexpected beta17 Baichuan UDP protocol")

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

            trace.distinct_msg_num_count = len(protocol._stream_msg_nums)
            trace.termination_reason = reason or "collector_stopped"
            trace.elapsed_seconds = round(self._loop.time() - started_at, 3)
            _RESULT_TRACE.set(_clone_trace(trace))
            return first
        except BaseException:
            trace.distinct_msg_num_count = len(protocol._stream_msg_nums)
            if not trace.termination_reason:
                trace.termination_reason = reason or "collector_exception"
            trace.elapsed_seconds = round(self._loop.time() - started_at, 3)
            _RESULT_TRACE.set(_clone_trace(trace))
            raise
        finally:
            protocol.clear_stream_probe()
            self._stream_decryptor = None


def _build_cmd13_wire(
    baichuan: Any,
    uid: str,
    candidate: RecordingCandidate,
):
    """Keep beta.16 wire unchanged and attach only the ephemeral decryptor."""
    wire, request, identity = _beta16_build_cmd13_wire(baichuan, uid, candidate)
    connection = getattr(baichuan, "_connection", None)
    if isinstance(connection, _StreamProbeConnection):
        connection.prepare_stream_probe(baichuan._aes_decrypt)
    return wire, request, identity


async def async_prepare_download_for_event(*args, **kwargs) -> DownloadStreamProbeResult:
    """Run the accepted beta.16 cmd13 and sample its same-session continuation."""
    token = _RESULT_TRACE.set(None)
    try:
        try:
            prepare = await base.async_prepare_download_for_event(*args, **kwargs)
        except base.CameraStageError as err:
            trace = _RESULT_TRACE.get()
            if trace is not None:
                setattr(err, "stream_trace", _clone_trace(trace))
            raise
        trace = _RESULT_TRACE.get() or StreamProbeTrace(
            attempted=True,
            termination_reason="collector_not_started",
        )
        return DownloadStreamProbeResult(prepare=prepare, stream_trace=_clone_trace(trace))
    finally:
        _RESULT_TRACE.reset(token)


# Patch only runtime extension points used by the base coroutine.  Search/auth/
# candidate selection/finally-close behavior remains the existing implementation.
base.BoundBaichuanUdpConnection = _StreamProbeConnection
base._build_cmd13_wire = _build_cmd13_wire

# Re-export the existing secret-safe helpers expected by button.py.
apply_file_info_trace = base.apply_file_info_trace
apply_identity_trace = base.apply_identity_trace
download_prepare_state = base.download_prepare_state
