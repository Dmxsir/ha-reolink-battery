"""Experimental bounded Baichuan live-view probe for the RTSP PoC branch.

This module is intentionally isolated from the production recording path. It
opens one UID/LAN UDP session, authenticates, sends the Baichuan Preview start
command (cmd3), observes a bounded window of BcMedia traffic, sends cmd4 to stop
the preview, and closes the session. It does not expose RTSP yet and never
keeps a sleeping battery camera connected in the background.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Callable, Literal

from reolink_aio.api import Host
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError

from . import recording_download_probe_beta20 as beta20
from .camera import CameraStageError, prepare_standalone_channel_zero
from .transport import (
    BAICHUAN_MAGIC,
    UID_RESOLVE_TIMEOUT_SECONDS,
    UidResolveTrace,
    linux_ipv4_interface,
    resolve_uid_lan,
    validate_local_lan_route,
)

LIVE_START_CMD_ID = 3
LIVE_STOP_CMD_ID = 4
LIVE_MESSAGE_CLASS = 0x6414
LIVE_CHANNEL_ID = 0
LIVE_MESSAGE_NUM_MODULUS = 1 << 16
DEFAULT_PROBE_SECONDS = 10.0
MAX_PROBE_SECONDS = 30.0
START_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 2.0

StreamKind = Literal["main", "sub"]


@dataclass(frozen=True, slots=True)
class LiveRequestMetadata:
    """Non-secret shape of a cmd3/cmd4 Preview request."""

    cmd_id: int
    header_channel_id: int
    stream_type: int
    msg_num: int
    message_class: int
    body_length: int
    payload_offset: int
    preview_handle: int
    preview_stream_type: str | None


@dataclass(frozen=True, slots=True)
class _RawLiveFrame:
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


@dataclass(slots=True)
class LiveStreamTrace:
    """Secret-safe telemetry for one bounded live-view attempt."""

    attempted: bool = False
    stream_kind: str = "main"
    start_attempted: bool = False
    start_response_code: int | None = None
    start_accepted: bool = False
    first_cmd3_delay_ms: float | None = None
    cmd3_frames: int = 0
    body_frames: int = 0
    total_body_bytes: int = 0
    bcmedia_observed: bool = False
    bcmedia_info_frames: int = 0
    video_frames: int = 0
    iframe_frames: int = 0
    pframe_frames: int = 0
    h264_frames: int = 0
    h265_frames: int = 0
    unknown_body_frames: int = 0
    stop_attempted: bool = False
    stop_response_code: int | None = None
    stop_accepted: bool = False
    remote_disconnect_observed: bool = False
    connection_lost_exception_present: bool = False
    p2p_heartbeat_count: int = 0
    udp_network_bc_datagrams_received: int = 0
    udp_seq_gap_events: int = 0
    udp_recovered_missing_packet_count: int = 0
    udp_unresolved_missing_packet_count_at_disconnect: int = 0
    elapsed_seconds: float | None = None
    termination_reason: str = ""
    raw_values_exposed: bool = False


@dataclass(frozen=True, slots=True)
class LiveStreamProbeResult:
    """Result of one bounded live-view probe."""

    start_request: LiveRequestMetadata
    stop_request: LiveRequestMetadata
    trace: LiveStreamTrace
    uid_resolve_trace: UidResolveTrace


class LiveStreamProbeError(CameraStageError):
    """Secret-safe error raised by the bounded live-stream probe."""

    def __init__(
        self,
        stage: str,
        *,
        failure_type: str = "",
        response_code: int | None = None,
        trace: LiveStreamTrace | None = None,
        uid_resolve_trace: UidResolveTrace | None = None,
    ) -> None:
        super().__init__(stage)
        self.failure_type = failure_type
        self.response_code = response_code
        self.trace = trace
        self.uid_resolve_trace = uid_resolve_trace


def _next_live_msg_num(baichuan: Any) -> int:
    current = int(getattr(baichuan, "_mess_id", 0))
    msg_num = (current + 1) % LIVE_MESSAGE_NUM_MODULUS
    baichuan._mess_id = msg_num
    return msg_num


def _stream_layout(stream: StreamKind) -> tuple[int, int, str]:
    if stream == "main":
        return 0, 0, "mainStream"
    if stream == "sub":
        # Neolink uses stream code 1 and handle 256 for Baichuan sub-stream.
        return 1, 256, "subStream"
    raise ValueError(f"unsupported stream kind: {stream}")


def _preview_xml(
    *, channel_id: int, handle: int, stream_name: str | None
) -> bytes:
    stream_xml = (
        f"<streamType>{stream_name}</streamType>" if stream_name is not None else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        "<body>\n"
        '<Preview version="1.1">'
        f"<channelId>{channel_id}</channelId>"
        f"<handle>{handle}</handle>"
        f"{stream_xml}"
        "</Preview>\n"
        "</body>"
    ).encode("utf-8")


def _build_preview_wire(
    baichuan: Any,
    *,
    cmd_id: int,
    stream: StreamKind,
    msg_num: int | None = None,
) -> tuple[bytes, LiveRequestMetadata]:
    stream_code, handle, stream_name = _stream_layout(stream)
    if msg_num is None:
        msg_num = _next_live_msg_num(baichuan)
    payload = _preview_xml(
        channel_id=LIVE_CHANNEL_ID,
        handle=handle,
        stream_name=stream_name if cmd_id == LIVE_START_CMD_ID else None,
    )
    body = baichuan._aes_encrypt(payload)
    header = (
        BAICHUAN_MAGIC
        + cmd_id.to_bytes(4, "little")
        + len(body).to_bytes(4, "little")
        + LIVE_CHANNEL_ID.to_bytes(1, "little")
        + stream_code.to_bytes(1, "little")
        + msg_num.to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + LIVE_MESSAGE_CLASS.to_bytes(2, "little")
        + (0).to_bytes(4, "little")
    )
    return (
        header + body,
        LiveRequestMetadata(
            cmd_id=cmd_id,
            header_channel_id=LIVE_CHANNEL_ID,
            stream_type=stream_code,
            msg_num=msg_num,
            message_class=LIVE_MESSAGE_CLASS,
            body_length=len(body),
            payload_offset=0,
            preview_handle=handle,
            preview_stream_type=(
                stream_name if cmd_id == LIVE_START_CMD_ID else None
            ),
        ),
    )


def _bcmedia_marker(data: bytes) -> tuple[str, str | None] | None:
    """Classify a BcMedia packet beginning without retaining payload bytes."""
    if len(data) < 4:
        return None
    magic = data[:4]
    if magic in {b"1001", b"1002"}:
        return ("info", None)
    if magic[0:1].isdigit() and magic[1:2].isdigit() and magic[2:4] == b"dc":
        channel_code = int(magic[:2])
        frame_kind = ""
        if 0 <= channel_code <= 9:
            frame_kind = "iframe"
        elif 10 <= channel_code <= 19:
            frame_kind = "pframe"
        if frame_kind:
            codec = None
            if len(data) >= 8 and data[4:8] in {b"H264", b"H265"}:
                codec = data[4:8].decode("ascii")
            return (frame_kind, codec)
    return None


class _LiveStreamProbeProtocol(beta20._P2PHeartbeatProbeProtocol):
    """Observe cmd3/cmd4 frames before reolink-aio's generic parser sees them."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._live_msg_num: int | None = None
        self._live_trace: LiveStreamTrace | None = None
        self._live_observer: Callable[[_RawLiveFrame], None] | None = None
        self._live_start_future: asyncio.Future[_RawLiveFrame] | None = None
        self._live_stop_future: asyncio.Future[_RawLiveFrame] | None = None
        self._live_started_at: float | None = None

    def arm_live_probe(
        self,
        msg_num: int,
        trace: LiveStreamTrace,
        observer: Callable[[_RawLiveFrame], None],
    ) -> tuple[asyncio.Future[_RawLiveFrame], asyncio.Future[_RawLiveFrame]]:
        self._live_msg_num = msg_num
        self._live_trace = trace
        self._live_observer = observer
        self._live_start_future = self._loop.create_future()
        self._live_stop_future = self._loop.create_future()
        self._live_started_at = self._loop.time()
        return self._live_start_future, self._live_stop_future

    def clear_live_probe(self) -> None:
        for future in (self._live_start_future, self._live_stop_future):
            if future is not None and not future.done():
                future.cancel()
        self._live_msg_num = None
        self._live_trace = None
        self._live_observer = None
        self._live_start_future = None
        self._live_stop_future = None
        self._live_started_at = None

    def _parse_live_frame(self, raw: bytes) -> _RawLiveFrame | None:
        expected = self._live_msg_num
        if expected is None or len(raw) < 20:
            return None
        cmd_id = int.from_bytes(raw[4:8], "little")
        if cmd_id not in (LIVE_START_CMD_ID, LIVE_STOP_CMD_ID):
            return None
        body_length = int.from_bytes(raw[8:12], "little")
        header_channel_id = raw[12]
        stream_type = raw[13]
        msg_num = int.from_bytes(raw[14:16], "little")
        if msg_num != expected:
            return None
        response_code = int.from_bytes(raw[16:18], "little")
        message_class = int.from_bytes(raw[18:20], "little")
        header_length = 24 if message_class in (0x0000, 0x6414, 0x6482) else 20
        if len(raw) < header_length + body_length:
            return None
        payload_offset = (
            int.from_bytes(raw[20:24], "little") if header_length == 24 else 0
        )
        payload_offset = min(payload_offset, body_length)
        return _RawLiveFrame(
            cmd_id=cmd_id,
            response_code=response_code,
            message_class=message_class,
            header_channel_id=header_channel_id,
            stream_type=stream_type,
            msg_num=msg_num,
            body_length=body_length,
            payload_offset=payload_offset,
            header=raw[:header_length],
            body=raw[header_length : header_length + body_length],
        )

    def parse_bc_data(self) -> None:
        frame = self._parse_live_frame(self._data)
        if frame is None:
            super().parse_bc_data()
            return

        trace = self._live_trace
        if trace is not None and frame.cmd_id == LIVE_START_CMD_ID:
            trace.cmd3_frames += 1
            trace.total_body_bytes += frame.body_length
            if frame.body_length:
                trace.body_frames += 1
            if trace.first_cmd3_delay_ms is None and self._live_started_at is not None:
                trace.first_cmd3_delay_ms = round(
                    max(0.0, self._loop.time() - self._live_started_at) * 1000.0,
                    3,
                )

        if self._live_observer is not None:
            self._live_observer(frame)

        if frame.cmd_id == LIVE_START_CMD_ID:
            future = self._live_start_future
            if future is not None and not future.done():
                future.set_result(frame)
        else:
            future = self._live_stop_future
            if future is not None and not future.done():
                future.set_result(frame)

        # cmd3/cmd4 use channel/stream/msgNum16. The stock parser interprets
        # bytes 12..15 as ch-id + a 24-bit message-id, so consume these here.
        self._data = b""

    def connection_lost(self, exc: Exception | None = None) -> None:
        for future in (self._live_start_future, self._live_stop_future):
            if future is not None and not future.done():
                future.set_exception(
                    exc or ConnectionError("live stream connection closed")
                )
        super().connection_lost(exc)


class _LiveStreamConnection(beta20._P2PHeartbeatFullTransferConnection):
    """Proven UDP/P2P lease handling plus bounded cmd3 live observation."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._live_decryptor: Callable[..., bytes | str] | None = None
        self._live_trace = LiveStreamTrace(attempted=True)

    async def _create_connection(self):
        handoff = self._take_handoff_socket()
        lease = None
        if handoff is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self.source_ip, 0))
        else:
            sock, lease = handoff
        try:
            sock.setblocking(False)
            created = await self._loop.create_datagram_endpoint(
                lambda: _LiveStreamProbeProtocol(
                    self._loop,
                    self._host,
                    self.drop_connection(),
                    self.cancel_ack_timeout,
                    self._push_callback,
                    self._close_callback,
                    reliable_ack_callback=self._observe_udp_ack,
                ),
                sock=sock,
            )
        except BaseException:
            sock.close()
            raise
        transport, protocol = created
        _, self._local_port = transport.get_extra_info("sockname")
        if lease is not None:
            self._apply_handoff_protocol(protocol, lease)
            self._p2p_heartbeat_tid = self._handoff_transaction_id
        return transport, protocol

    def prepare_live_probe(
        self,
        decryptor: Callable[..., bytes | str],
        *,
        stream_kind: StreamKind,
    ) -> LiveStreamTrace:
        self._live_decryptor = decryptor
        self._live_trace = LiveStreamTrace(attempted=True, stream_kind=stream_kind)
        return self._live_trace

    def _try_aes(self, data: bytes, header: bytes) -> bytes | None:
        if not data or self._live_decryptor is None:
            return None
        try:
            decoded = self._live_decryptor(data, header, decode=False)
        except Exception:
            return None
        return decoded if isinstance(decoded, bytes) else None

    def _classify_live_bytes(self, data: bytes) -> bool:
        marker = _bcmedia_marker(data)
        if marker is None:
            return False
        trace = self._live_trace
        trace.bcmedia_observed = True
        frame_kind, codec = marker
        if frame_kind == "info":
            trace.bcmedia_info_frames += 1
        else:
            trace.video_frames += 1
            if frame_kind == "iframe":
                trace.iframe_frames += 1
            elif frame_kind == "pframe":
                trace.pframe_frames += 1
            if codec == "H264":
                trace.h264_frames += 1
            elif codec == "H265":
                trace.h265_frames += 1
        return True

    def _observe_live_frame(self, frame: _RawLiveFrame) -> None:
        if frame.cmd_id != LIVE_START_CMD_ID or not frame.body:
            return
        body = frame.body
        candidates: list[bytes] = []
        if frame.payload_offset > 0:
            encrypted_extension = body[: frame.payload_offset]
            payload = body[frame.payload_offset :]
            extension = self._try_aes(encrypted_extension, frame.header) or b""
            encrypt_len = beta20.beta17._extension_encrypt_len(extension)
            if encrypt_len is not None and encrypt_len > 0 and payload:
                encrypt_len = min(encrypt_len, len(payload))
                prefix = self._try_aes(payload[:encrypt_len], frame.header)
                if prefix is not None:
                    candidates.append(prefix + payload[encrypt_len:])
            if payload:
                candidates.append(payload)
                decrypted = self._try_aes(payload, frame.header)
                if decrypted is not None:
                    candidates.append(decrypted)
        else:
            decrypted = self._try_aes(body, frame.header)
            if decrypted is not None:
                candidates.append(decrypted)
            candidates.append(body)

        for candidate in candidates:
            if self._classify_live_bytes(candidate):
                return
        self._live_trace.unknown_body_frames += 1

    def _copy_transport_trace(self, protocol: _LiveStreamProbeProtocol) -> None:
        trace = self._live_trace
        trace.remote_disconnect_observed = protocol.remote_disconnect_observed
        trace.connection_lost_exception_present = (
            protocol.connection_lost_exception_present
        )
        trace.p2p_heartbeat_count = self._p2p_heartbeat_total_count
        trace.udp_network_bc_datagrams_received = (
            protocol.udp_network_bc_datagrams_received
        )
        trace.udp_seq_gap_events = protocol.udp_seq_gap_events
        trace.udp_recovered_missing_packet_count = (
            protocol.udp_recovered_missing_packet_count
        )
        trace.udp_unresolved_missing_packet_count_at_disconnect = (
            protocol.udp_unresolved_missing_packet_count_at_disconnect
        )

    async def send_live_stream_probe(
        self,
        start_wire: bytes,
        stop_wire: bytes,
        *,
        msg_num: int,
        duration: float,
        start_timeout: float = START_TIMEOUT_SECONDS,
    ) -> LiveStreamTrace:
        if not self.connection_open:
            await self.connect()
        protocol = self._protocol
        if not isinstance(protocol, _LiveStreamProbeProtocol):
            raise RuntimeError("unexpected live-stream UDP protocol")

        trace = self._live_trace
        trace.start_attempted = True
        start_future, stop_future = protocol.arm_live_probe(
            msg_num, trace, self._observe_live_frame
        )
        started_at = self._loop.time()
        try:
            await self._send_reliable_download_packet(
                start_wire,
                cmd_id=LIVE_START_CMD_ID,
                response_future=start_future,
            )
            start_frame = await asyncio.wait_for(
                asyncio.shield(start_future),
                timeout=max(float(start_timeout), 1.0),
            )
            trace.start_response_code = start_frame.response_code
            trace.start_accepted = start_frame.response_code == 200
            if not trace.start_accepted:
                trace.termination_reason = f"start_response_{start_frame.response_code}"
                return trace

            deadline = self._loop.time() + max(0.1, min(duration, MAX_PROBE_SECONDS))
            while self.connection_open and self._loop.time() < deadline:
                await asyncio.sleep(0.05)

            trace.termination_reason = (
                "duration_reached" if self.connection_open else "connection_closed"
            )

            if self.connection_open:
                trace.stop_attempted = True
                try:
                    await self._send_reliable_download_packet(
                        stop_wire,
                        cmd_id=LIVE_STOP_CMD_ID,
                        response_future=stop_future,
                    )
                    stop_frame = await asyncio.wait_for(
                        asyncio.shield(stop_future), timeout=STOP_TIMEOUT_SECONDS
                    )
                    trace.stop_response_code = stop_frame.response_code
                    trace.stop_accepted = stop_frame.response_code == 200
                except (TimeoutError, asyncio.TimeoutError):
                    if not trace.termination_reason:
                        trace.termination_reason = "stop_timeout"
            return trace
        finally:
            trace.elapsed_seconds = round(self._loop.time() - started_at, 3)
            self._copy_transport_trace(protocol)
            protocol.clear_live_probe()
            self._live_decryptor = None


async def async_probe_live_stream(
    uid: str,
    username: str,
    password: str,
    interface: ipaddress.IPv4Interface,
    *,
    stream: StreamKind = "main",
    duration: float = DEFAULT_PROBE_SECONDS,
    resolve_timeout: float = UID_RESOLVE_TIMEOUT_SECONDS,
    command_timeout: int = 30,
) -> LiveStreamProbeResult:
    """Open one bounded live-view session and return secret-safe telemetry."""
    lease = None
    host = None
    connection: _LiveStreamConnection | None = None
    failure_stage = "UID_RESOLVE_ERROR"
    uid_trace = UidResolveTrace(timeout_seconds=float(resolve_timeout))
    trace = LiveStreamTrace(attempted=True, stream_kind=stream)

    try:
        interface_name, _ = await asyncio.to_thread(
            linux_ipv4_interface, str(interface.ip)
        )
        lease = await asyncio.to_thread(
            resolve_uid_lan, uid, interface, resolve_timeout, uid_trace
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
        connection = _LiveStreamConnection(
            lease.host,
            lease.source_ip,
            0,
            host.baichuan._push_callback,
            host.baichuan._close_callback,
            uid=uid,
            handoff_lease=lease,
        )
        host.baichuan._connection = connection

        failure_stage = "LIVE_WAKE_ERROR"
        await connection.connect()
        if lease.socket is not None:
            raise RuntimeError("single lease handoff was not adopted")
        lease = None

        failure_stage = "LIVE_AUTH_ERROR"
        host.baichuan._first_login = False
        await host.baichuan.login()
        connection.activate_fresh_heartbeat_tids_after_login()

        trace = connection.prepare_live_probe(
            host.baichuan._aes_decrypt, stream_kind=stream
        )
        start_wire, start_request = _build_preview_wire(
            host.baichuan, cmd_id=LIVE_START_CMD_ID, stream=stream
        )
        stop_wire, stop_request = _build_preview_wire(
            host.baichuan,
            cmd_id=LIVE_STOP_CMD_ID,
            stream=stream,
            msg_num=start_request.msg_num,
        )

        failure_stage = "LIVE_STREAM_START_ERROR"
        trace = await connection.send_live_stream_probe(
            start_wire,
            stop_wire,
            msg_num=start_request.msg_num,
            duration=duration,
            start_timeout=min(float(command_timeout), START_TIMEOUT_SECONDS),
        )
        if not trace.start_accepted:
            raise LiveStreamProbeError(
                "LIVE_STREAM_REJECTED",
                response_code=trace.start_response_code,
                trace=trace,
                uid_resolve_trace=uid_trace,
            )

        return LiveStreamProbeResult(
            start_request=start_request,
            stop_request=stop_request,
            trace=trace,
            uid_resolve_trace=uid_trace,
        )
    except LiveStreamProbeError:
        raise
    except (ReolinkError, OSError, TimeoutError, RuntimeError, ValueError) as err:
        rsp_code = getattr(err, "rspCode", None)
        if not isinstance(rsp_code, int):
            rsp_code = None
        raise LiveStreamProbeError(
            failure_stage,
            failure_type=type(err).__name__,
            response_code=rsp_code,
            trace=trace,
            uid_resolve_trace=uid_trace,
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
