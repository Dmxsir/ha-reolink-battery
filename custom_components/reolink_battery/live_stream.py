"""Battery-safe on-demand Baichuan live media transport.

This module contains only the Live View path. It deliberately subclasses the
production UID/LAN transport instead of modifying it, so recording downloads and
manual status sessions keep their existing protocol behavior.

Hardware-proven Argus 2E behavior included here:
- reuse the UID discovery socket for Baichuan login;
- send the discovery-layer P2P heartbeat before/during authentication;
- switch heartbeat transaction ids after login;
- send Baichuan cmd234 every 500 ms while media is active;
- use an inclusive selective UDP ACK bitmap;
- preserve multiple cmd3/cmd4 messages in one reassembled receive buffer;
- reassemble BcMedia across cmd3 message boundaries;
- forward H264 Annex-B and AAC/ADTS in memory only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import socket
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Callable, Literal

from reolink_aio.api import Host
from reolink_aio.baichuan.util import calc_crc, encrypt_udp_baichuan
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError

from .transport import (
    BAICHUAN_MAGIC,
    DISCOVERY_MAGIC,
    UID_RESOLVE_TIMEOUT_SECONDS,
    BoundBaichuanUdpConnection,
    UidResolveTrace,
    _IdempotentUdpClientProtocol,
    linux_ipv4_interface,
    resolve_uid_lan,
    validate_local_lan_route,
)

LIVE_START_CMD_ID = 3
LIVE_STOP_CMD_ID = 4
LIVE_MESSAGE_CLASS = 0x6414
LIVE_CHANNEL_ID = 0
LIVE_MESSAGE_NUM_MODULUS = 1 << 16
START_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 3.0
P2P_HEARTBEAT_INTERVAL_SECONDS = 1.0
UDP_MEDIA_KEEPALIVE_CMD_ID = 234
UDP_MEDIA_KEEPALIVE_INTERVAL_SECONDS = 0.5
ACCEPTED_RESPONSE_CODES = frozenset({0, 200})

_INFO_MAGICS = (b"1001", b"1002")
_VIDEO_MAGICS = tuple(
    f"{channel}{frame_type}dc".encode("ascii")
    for channel in range(10)
    for frame_type in (0, 1)
)
_AUDIO_MAGICS = (b"05wb", b"01wb")
_ALL_MAGICS = _INFO_MAGICS + _VIDEO_MAGICS + _AUDIO_MAGICS
_MAX_MEDIA_PACKET_BYTES = 4 * 1024 * 1024
_MAX_ADDITIONAL_HEADER_BYTES = 64 * 1024
_MAX_ROLLING_BUFFER_BYTES = 8 * 1024 * 1024
_MAGIC_UDP_ACK = bytes.fromhex("20cf872a")

StreamKind = Literal["main", "sub"]
H264FrameSink = Callable[[bytes, str], None]
AudioFrameSink = Callable[[bytes, str], None]


class LiveStreamError(RuntimeError):
    """A secret-safe live stream failure."""

    def __init__(
        self,
        stage: str,
        *,
        failure_type: str = "",
        response_code: int | None = None,
    ) -> None:
        super().__init__(stage)
        self.stage = stage
        self.failure_type = failure_type
        self.response_code = response_code


@dataclass(slots=True)
class LiveStreamTrace:
    """Small in-memory status record; raw media is never retained here."""

    start_accepted: bool = False
    start_response_code: int | None = None
    stop_accepted: bool = False
    stop_response_code: int | None = None
    h264_frames: int = 0
    aac_packets: int = 0
    p2p_heartbeat_count: int = 0
    media_keepalive_count: int = 0
    termination_reason: str = ""


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


def _prepare_standalone_channel_zero(host: Host) -> None:
    if 0 not in host._channels:
        host._channels.append(0)
    if 0 not in host._stream_channels:
        host._stream_channels.append(0)
    host._num_channels = max(host._num_channels, 1)


def _next_live_msg_num(baichuan: Any) -> int:
    current = int(getattr(baichuan, "_mess_id", 0))
    msg_num = (current + 1) % LIVE_MESSAGE_NUM_MODULUS
    baichuan._mess_id = msg_num
    return msg_num


def _stream_layout(stream: StreamKind) -> tuple[int, int, str]:
    if stream == "main":
        return 0, 0, "mainStream"
    if stream == "sub":
        return 1, 256, "subStream"
    raise ValueError(f"unsupported stream kind: {stream}")


def _preview_xml(*, handle: int, stream_name: str | None) -> bytes:
    stream_xml = (
        f"<streamType>{stream_name}</streamType>" if stream_name is not None else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<body>\n<Preview version="1.1">'
        f"<channelId>{LIVE_CHANNEL_ID}</channelId>"
        f"<handle>{handle}</handle>"
        f"{stream_xml}"
        "</Preview>\n</body>"
    ).encode("utf-8")


def _build_preview_wire(
    baichuan: Any,
    *,
    cmd_id: int,
    stream: StreamKind,
) -> tuple[bytes, int]:
    stream_code, handle, stream_name = _stream_layout(stream)
    msg_num = _next_live_msg_num(baichuan)
    payload = _preview_xml(
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
    return header + body, msg_num


def _extension_encrypt_len(extension: bytes) -> int | None:
    start_tag = b"<encryptLen>"
    end_tag = b"</encryptLen>"
    start = extension.find(start_tag)
    end = extension.find(end_tag)
    if start < 0 or end <= start:
        return None
    try:
        return int(extension[start + len(start_tag) : end].strip())
    except ValueError:
        return None


def _encode_p2p_heartbeat(
    transaction_id: int, client_id: int, device_id: int
) -> bytes:
    xml = (
        "<P2P><C2D_HB>"
        f"<cid>{client_id}</cid><did>{device_id}</did>"
        "</C2D_HB></P2P>"
    )
    payload = encrypt_udp_baichuan(xml, transaction_id)
    return (
        DISCOVERY_MAGIC
        + len(payload).to_bytes(4, "little")
        + bytes.fromhex("01000000")
        + transaction_id.to_bytes(4, "little")
        + calc_crc(payload)
        + payload
    )


def _padding8(payload_size: int) -> int:
    remainder = payload_size % 8
    return 0 if remainder == 0 else 8 - remainder


def _earliest_magic_offset(data: bytearray) -> int | None:
    offsets = [offset for magic in _ALL_MAGICS if (offset := data.find(magic)) >= 0]
    return min(offsets) if offsets else None


def _parse_rolling_bcmedia(
    buffer: bytearray,
    trace: LiveStreamTrace,
    h264_sink: H264FrameSink | None,
    audio_sink: AudioFrameSink | None,
) -> int:
    """Consume every complete BcMedia packet currently available."""
    parsed = 0
    while buffer:
        offset = _earliest_magic_offset(buffer)
        if offset is None:
            if len(buffer) > 3:
                del buffer[:-3]
            break
        if offset:
            del buffer[:offset]
        if len(buffer) < 4:
            break
        magic = bytes(buffer[:4])

        if magic in _INFO_MAGICS:
            if len(buffer) < 32:
                break
            if int.from_bytes(buffer[4:8], "little") != 32:
                del buffer[0]
                continue
            del buffer[:32]
            parsed += 1
            continue

        if magic in _VIDEO_MAGICS:
            if len(buffer) < 24:
                break
            codec = bytes(buffer[4:8])
            if codec not in (b"H264", b"H265"):
                del buffer[0]
                continue
            payload_size = int.from_bytes(buffer[8:12], "little")
            additional_header_size = int.from_bytes(buffer[12:16], "little")
            if (
                payload_size <= 0
                or payload_size > _MAX_MEDIA_PACKET_BYTES
                or additional_header_size > _MAX_ADDITIONAL_HEADER_BYTES
            ):
                del buffer[0]
                continue
            packet_size = 24 + additional_header_size + payload_size + _padding8(payload_size)
            if packet_size > _MAX_MEDIA_PACKET_BYTES + _MAX_ADDITIONAL_HEADER_BYTES + 32:
                del buffer[0]
                continue
            if len(buffer) < packet_size:
                break
            frame_type = "iframe" if magic[1:2] == b"0" else "pframe"
            if codec == b"H264":
                payload_start = 24 + additional_header_size
                payload_end = payload_start + payload_size
                payload = bytes(buffer[payload_start:payload_end])
                trace.h264_frames += 1
                if h264_sink is not None:
                    h264_sink(payload, frame_type)
            del buffer[:packet_size]
            parsed += 1
            continue

        if magic in _AUDIO_MAGICS:
            if len(buffer) < 8:
                break
            payload_size = int.from_bytes(buffer[4:6], "little")
            duplicate_size = int.from_bytes(buffer[6:8], "little")
            if (
                payload_size <= 0
                or payload_size != duplicate_size
                or payload_size > _MAX_MEDIA_PACKET_BYTES
            ):
                del buffer[0]
                continue
            packet_size = 8 + payload_size + _padding8(payload_size)
            if len(buffer) < packet_size:
                break
            payload = bytes(buffer[8 : 8 + payload_size])
            codec_name = "aac" if magic == b"05wb" else "adpcm"
            if codec_name == "aac":
                trace.aac_packets += 1
            if audio_sink is not None:
                audio_sink(payload, codec_name)
            del buffer[:packet_size]
            parsed += 1
            continue

        del buffer[0]
    return parsed


class _LiveStreamProtocol(_IdempotentUdpClientProtocol):
    """Baichuan parser with live framing and the proven inclusive UDP ACK."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._live_msg_num: int | None = None
        self._live_observer: Callable[[_RawLiveFrame], None] | None = None
        self._live_start_future: asyncio.Future[_RawLiveFrame] | None = None
        self._live_stop_future: asyncio.Future[_RawLiveFrame] | None = None

    def arm_live(
        self,
        start_msg_num: int,
        observer: Callable[[_RawLiveFrame], None],
    ) -> tuple[asyncio.Future[_RawLiveFrame], asyncio.Future[_RawLiveFrame]]:
        self._live_msg_num = start_msg_num
        self._live_observer = observer
        self._live_start_future = self._loop.create_future()
        self._live_stop_future = self._loop.create_future()
        return self._live_start_future, self._live_stop_future

    def clear_live(self) -> None:
        for future in (self._live_start_future, self._live_stop_future):
            if future is not None and not future.done():
                future.cancel()
        self._live_msg_num = None
        self._live_observer = None
        self._live_start_future = None
        self._live_stop_future = None

    def _live_frame(self, raw: bytes) -> _RawLiveFrame | None:
        if self._live_msg_num is None or len(raw) < 20:
            return None
        cmd_id = int.from_bytes(raw[4:8], "little")
        if cmd_id not in (LIVE_START_CMD_ID, LIVE_STOP_CMD_ID):
            return None
        body_length = int.from_bytes(raw[8:12], "little")
        msg_num = int.from_bytes(raw[14:16], "little")
        if cmd_id == LIVE_START_CMD_ID and msg_num != self._live_msg_num:
            return None
        message_class = int.from_bytes(raw[18:20], "little")
        header_length = 24 if message_class in (0x0000, 0x6414, 0x6482) else 20
        if len(raw) < header_length + body_length:
            return None
        payload_offset = int.from_bytes(raw[20:24], "little") if header_length == 24 else 0
        payload_offset = min(payload_offset, body_length)
        return _RawLiveFrame(
            cmd_id=cmd_id,
            response_code=int.from_bytes(raw[16:18], "little"),
            message_class=message_class,
            header_channel_id=raw[12],
            stream_type=raw[13],
            msg_num=msg_num,
            body_length=body_length,
            payload_offset=payload_offset,
            header=raw[:header_length],
            body=raw[header_length : header_length + body_length],
        )

    def parse_bc_data(self) -> None:
        while True:
            raw = self._data
            frame = self._live_frame(raw)
            if frame is None:
                super().parse_bc_data()
                return
            header_length = 24 if frame.message_class in (0x0000, 0x6414, 0x6482) else 20
            consumed = header_length + frame.body_length
            if consumed > len(raw):
                return
            if self._live_observer is not None:
                self._live_observer(frame)
            future = self._live_start_future if frame.cmd_id == LIVE_START_CMD_ID else self._live_stop_future
            if future is not None and not future.done():
                future.set_result(frame)
            self._data = raw[consumed:]
            if not self._data:
                return

    def send_ack(self) -> None:
        transport = getattr(self, "_transport", None)
        host_id = getattr(self, "host_id", None)
        recv_seq_id = int(getattr(self, "_recv_seq_id", -1))
        if transport is None or host_id is None:
            super().send_ack()
            return
        if recv_seq_id < 0:
            return
        pending = getattr(self, "_seq_data", {})
        payload = b""
        if pending:
            highest = max(pending)
            if highest > recv_seq_id:
                payload = bytes(
                    1 if seq_id in pending else 0
                    for seq_id in range(recv_seq_id + 1, highest + 1)
                )
        udp_header = (
            _MAGIC_UDP_ACK
            + int(host_id).to_bytes(4, "little")
            + bytes.fromhex("0000000000000000")
            + recv_seq_id.to_bytes(4, "little")
            + bytes.fromhex("00000000")
            + len(payload).to_bytes(4, "little")
        )
        transport.sendto(udp_header + payload, (self._host, self.remote_port))

    def connection_lost(self, exc: Exception | None = None) -> None:
        for future in (self._live_start_future, self._live_stop_future):
            if future is not None and not future.done():
                future.set_exception(exc or ConnectionError("live stream connection closed"))
        super().connection_lost(exc)


class _LiveStreamConnection(BoundBaichuanUdpConnection):
    """Dedicated live transport; production recording transport is untouched."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._live_decryptor: Callable[..., bytes | str] | None = None
        self._trace = LiveStreamTrace()
        self._media_buffer = bytearray()
        self._h264_sink: H264FrameSink | None = None
        self._audio_sink: AudioFrameSink | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._fresh_heartbeat_tid_enabled = False
        self._media_keepalive_task: asyncio.Task[None] | None = None
        self._media_keepalive_msg_num: int | None = None

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
            transport, protocol = await self._loop.create_datagram_endpoint(
                lambda: _LiveStreamProtocol(
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
        _, self._local_port = transport.get_extra_info("sockname")
        if lease is not None:
            self._apply_handoff_protocol(protocol, lease)
        return transport, protocol

    async def connect(self):
        await super().connect()
        if getattr(self, "_handoff_active", False):
            self.start_heartbeat()

    def activate_after_login(
        self,
        decryptor: Callable[..., bytes | str],
        *,
        h264_sink: H264FrameSink,
        audio_sink: AudioFrameSink | None,
    ) -> LiveStreamTrace:
        self._live_decryptor = decryptor
        self._h264_sink = h264_sink
        self._audio_sink = audio_sink
        self._media_buffer = bytearray()
        self._fresh_heartbeat_tid_enabled = True
        return self._trace

    def _try_aes(self, data: bytes, header: bytes) -> bytes | None:
        if not data or self._live_decryptor is None:
            return None
        try:
            decoded = self._live_decryptor(data, header, decode=False)
        except Exception:
            return None
        return decoded if isinstance(decoded, bytes) else None

    def _media_chunk_from_frame(self, frame: _RawLiveFrame) -> bytes:
        body = frame.body
        if not body:
            return b""
        if frame.payload_offset <= 0:
            return body
        enc_extension = body[: frame.payload_offset]
        payload = body[frame.payload_offset :]
        if not payload:
            return b""
        extension = self._try_aes(enc_extension, frame.header) or b""
        encrypt_len = _extension_encrypt_len(extension)
        if encrypt_len and encrypt_len > 0:
            encrypt_len = min(encrypt_len, len(payload))
            decrypted_prefix = self._try_aes(payload[:encrypt_len], frame.header)
            if decrypted_prefix is not None:
                return decrypted_prefix + payload[encrypt_len:]
        return payload

    def observe_live_frame(self, frame: _RawLiveFrame) -> None:
        if frame.cmd_id != LIVE_START_CMD_ID or not frame.body:
            return
        chunk = self._media_chunk_from_frame(frame)
        if not chunk:
            return
        self._media_buffer.extend(chunk)
        if len(self._media_buffer) > _MAX_ROLLING_BUFFER_BYTES:
            del self._media_buffer[: len(self._media_buffer) - _MAX_ROLLING_BUFFER_BYTES]
        _parse_rolling_bcmedia(
            self._media_buffer,
            self._trace,
            self._h264_sink,
            self._audio_sink,
        )

    def _send_p2p_heartbeat(self) -> bool:
        protocol = self._protocol
        transport = self._transport
        if (
            not isinstance(protocol, _LiveStreamProtocol)
            or transport is None
            or protocol.client_id is None
            or protocol.host_id is None
        ):
            return False
        if self._fresh_heartbeat_tid_enabled:
            transaction_id = secrets.randbelow(999_000) + 1_000
        else:
            transaction_id = getattr(self, "_handoff_transaction_id", None)
            if not isinstance(transaction_id, int):
                transaction_id = secrets.randbelow(999_000) + 1_000
        packet = _encode_p2p_heartbeat(
            transaction_id,
            protocol.client_id,
            protocol.host_id,
        )
        transport.sendto(packet, (self._host, self._port))
        self._trace.p2p_heartbeat_count += 1
        return True

    async def _heartbeat_loop(self) -> None:
        try:
            while self.connection_open:
                await asyncio.sleep(P2P_HEARTBEAT_INTERVAL_SECONDS)
                if self.connection_open:
                    self._send_p2p_heartbeat()
        except asyncio.CancelledError:
            raise

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._send_p2p_heartbeat()
        self._heartbeat_task = self._loop.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _build_media_keepalive_wire(self) -> bytes:
        if self._media_keepalive_msg_num is None:
            self._media_keepalive_msg_num = secrets.randbelow(65536)
        return (
            BAICHUAN_MAGIC
            + UDP_MEDIA_KEEPALIVE_CMD_ID.to_bytes(4, "little")
            + (0).to_bytes(4, "little")
            + (0).to_bytes(1, "little")
            + (0).to_bytes(1, "little")
            + self._media_keepalive_msg_num.to_bytes(2, "little")
            + (0).to_bytes(2, "little")
            + LIVE_MESSAGE_CLASS.to_bytes(2, "little")
            + (0).to_bytes(4, "little")
        )

    async def _send_media_keepalive(self) -> None:
        if not self.connection_open:
            return
        try:
            await self.send_without_wait(
                self._build_media_keepalive_wire(),
                cmd_id=UDP_MEDIA_KEEPALIVE_CMD_ID,
                timeout=2,
            )
        except (ReolinkError, OSError, TimeoutError, asyncio.TimeoutError):
            return
        self._trace.media_keepalive_count += 1

    async def _media_keepalive_loop(self) -> None:
        try:
            while self.connection_open:
                await asyncio.sleep(UDP_MEDIA_KEEPALIVE_INTERVAL_SECONDS)
                if self.connection_open:
                    await self._send_media_keepalive()
        except asyncio.CancelledError:
            raise

    async def start_media_keepalive(self) -> None:
        if self._media_keepalive_task is not None and not self._media_keepalive_task.done():
            return
        await self._send_media_keepalive()
        self._media_keepalive_task = self._loop.create_task(self._media_keepalive_loop())

    async def stop_media_keepalive(self) -> None:
        task = self._media_keepalive_task
        self._media_keepalive_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def close(self) -> None:
        await self.stop_media_keepalive()
        await self.stop_heartbeat()
        await super().close()


async def async_stream_media(
    uid: str,
    username: str,
    password: str,
    interface: ipaddress.IPv4Interface,
    *,
    h264_sink: H264FrameSink,
    audio_sink: AudioFrameSink | None,
    stop_event: asyncio.Event,
    stream: StreamKind = "main",
    resolve_timeout: float = UID_RESOLVE_TIMEOUT_SECONDS,
    command_timeout: int = 30,
) -> LiveStreamTrace:
    """Wake the camera on demand and stream until the final consumer disconnects."""
    lease = None
    host = None
    connection: _LiveStreamConnection | None = None
    failure_stage = "UID_RESOLVE_ERROR"
    try:
        interface_name, _ = await asyncio.to_thread(linux_ipv4_interface, str(interface.ip))
        uid_trace = UidResolveTrace(timeout_seconds=float(resolve_timeout))
        lease = await asyncio.to_thread(
            resolve_uid_lan,
            uid,
            interface,
            resolve_timeout,
            uid_trace,
        )
        await asyncio.to_thread(
            validate_local_lan_route,
            interface,
            lease.host,
            interface_name,
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
        _prepare_standalone_channel_zero(host)
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
            raise RuntimeError("single UID lease handoff was not adopted")
        lease = None

        failure_stage = "LIVE_AUTH_ERROR"
        host.baichuan._first_login = False
        await host.baichuan.login()
        trace = connection.activate_after_login(
            host.baichuan._aes_decrypt,
            h264_sink=h264_sink,
            audio_sink=audio_sink,
        )

        start_wire, start_msg_num = _build_preview_wire(
            host.baichuan,
            cmd_id=LIVE_START_CMD_ID,
            stream=stream,
        )
        stop_wire, _ = _build_preview_wire(
            host.baichuan,
            cmd_id=LIVE_STOP_CMD_ID,
            stream=stream,
        )
        protocol = connection._protocol
        if not isinstance(protocol, _LiveStreamProtocol):
            raise RuntimeError("unexpected live-stream UDP protocol")
        start_future, stop_future = protocol.arm_live(
            start_msg_num,
            connection.observe_live_frame,
        )
        await connection.start_media_keepalive()

        failure_stage = "LIVE_STREAM_START_ERROR"
        await connection.send_without_wait(start_wire, cmd_id=LIVE_START_CMD_ID, timeout=5)
        start_frame = await asyncio.wait_for(
            asyncio.shield(start_future),
            timeout=START_TIMEOUT_SECONDS,
        )
        trace.start_response_code = start_frame.response_code
        trace.start_accepted = start_frame.response_code in ACCEPTED_RESPONSE_CODES
        if not trace.start_accepted:
            raise LiveStreamError(
                "LIVE_STREAM_REJECTED",
                response_code=trace.start_response_code,
            )

        while connection.connection_open and not stop_event.is_set():
            await asyncio.sleep(0.05)

        if connection.connection_open:
            with suppress(TimeoutError, asyncio.TimeoutError, ReolinkError, OSError):
                await connection.send_without_wait(stop_wire, cmd_id=LIVE_STOP_CMD_ID, timeout=2)
                stop_frame = await asyncio.wait_for(
                    asyncio.shield(stop_future),
                    timeout=STOP_TIMEOUT_SECONDS,
                )
                trace.stop_response_code = stop_frame.response_code
                trace.stop_accepted = stop_frame.response_code in ACCEPTED_RESPONSE_CODES

        trace.termination_reason = (
            "consumer_disconnected" if stop_event.is_set() else "connection_closed"
        )
        return trace
    except LiveStreamError:
        raise
    except (
        ReolinkError,
        OSError,
        TimeoutError,
        asyncio.TimeoutError,
        RuntimeError,
        ValueError,
    ) as err:
        rsp_code = getattr(err, "rspCode", None)
        raise LiveStreamError(
            failure_stage,
            failure_type=type(err).__name__,
            response_code=rsp_code if isinstance(rsp_code, int) else None,
        ) from None
    finally:
        stop_event.set()
        if connection is not None:
            connection._h264_sink = None
            connection._audio_sink = None
            protocol = connection._protocol
            if isinstance(protocol, _LiveStreamProtocol):
                protocol.clear_live()
            connection._live_decryptor = None
        try:
            if host is not None:
                with suppress(ReolinkError, OSError, TimeoutError, asyncio.TimeoutError):
                    await host.logout()
        finally:
            try:
                if connection is not None and connection.connection_open:
                    with suppress(ReolinkError, OSError, TimeoutError, asyncio.TimeoutError):
                        await connection.close()
            finally:
                if lease is not None:
                    lease.close()
                password = ""
