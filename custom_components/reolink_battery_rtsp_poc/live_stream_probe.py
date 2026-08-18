"""Bounded, fully isolated Baichuan live-view probe.

The PoC owns its UID/LAN transport and never imports code from the production
`reolink_battery` package. The only runtime coupling is performed by button.py,
which reads the already configured source entry values and shares its operation
lock so recording downloads and Live View cannot run concurrently.
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import socket
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
DEFAULT_PROBE_SECONDS = 10.0
MAX_PROBE_SECONDS = 30.0
START_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 3.0
HEARTBEAT_INTERVAL_SECONDS = 1.0
ACCEPTED_RESPONSE_CODES = frozenset({0, 200})

StreamKind = Literal["main", "sub"]


class LiveStreamProbeError(RuntimeError):
    """Secret-safe live-view failure."""

    def __init__(
        self,
        stage: str,
        *,
        failure_type: str = "",
        response_code: int | None = None,
        trace: "LiveStreamTrace | None" = None,
        uid_resolve_trace: UidResolveTrace | None = None,
    ) -> None:
        super().__init__(stage)
        self.stage = stage
        self.failure_type = failure_type
        self.response_code = response_code
        self.trace = trace
        self.uid_resolve_trace = uid_resolve_trace


@dataclass(frozen=True, slots=True)
class LiveRequestMetadata:
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
    heartbeat_count: int = 0
    connection_lost_exception_present: bool = False
    elapsed_seconds: float | None = None
    termination_reason: str = ""
    raw_values_exposed: bool = False


@dataclass(frozen=True, slots=True)
class LiveStreamProbeResult:
    start_request: LiveRequestMetadata
    stop_request: LiveRequestMetadata
    trace: LiveStreamTrace
    uid_resolve_trace: UidResolveTrace


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
    msg_num: int | None = None,
) -> tuple[bytes, LiveRequestMetadata]:
    stream_code, handle, stream_name = _stream_layout(stream)
    if msg_num is None:
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
    return header + body, LiveRequestMetadata(
        cmd_id=cmd_id,
        header_channel_id=LIVE_CHANNEL_ID,
        stream_type=stream_code,
        msg_num=msg_num,
        message_class=LIVE_MESSAGE_CLASS,
        body_length=len(body),
        payload_offset=0,
        preview_handle=handle,
        preview_stream_type=(stream_name if cmd_id == LIVE_START_CMD_ID else None),
    )


def _scan_bcmedia(data: bytes, trace: LiveStreamTrace) -> bool:
    found = False

    for marker in (b"1001", b"1002"):
        count = data.count(marker)
        if count:
            trace.bcmedia_info_frames += count
            found = True

    for channel in range(10):
        prefix = f"{channel:02d}dc".encode("ascii")
        for codec in (b"H264", b"H265"):
            count = data.count(prefix + codec)
            if not count:
                continue
            trace.iframe_frames += count
            trace.video_frames += count
            if codec == b"H264":
                trace.h264_frames += count
            else:
                trace.h265_frames += count
            found = True

    for channel in range(10, 20):
        prefix = f"{channel:02d}dc".encode("ascii")
        for codec in (b"H264", b"H265"):
            count = data.count(prefix + codec)
            if not count:
                continue
            trace.pframe_frames += count
            trace.video_frames += count
            if codec == b"H264":
                trace.h264_frames += count
            else:
                trace.h265_frames += count
            found = True

    if found:
        trace.bcmedia_observed = True
    return found


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


class _LiveStreamProtocol(_IdempotentUdpClientProtocol):
    """Observe cmd3/cmd4 while leaving login/control parsing untouched."""

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

    def _live_frame(self, raw: bytes) -> _RawLiveFrame | None:
        if self._live_msg_num is None or len(raw) < 20:
            return None
        cmd_id = int.from_bytes(raw[4:8], "little")
        if cmd_id not in (LIVE_START_CMD_ID, LIVE_STOP_CMD_ID):
            return None

        body_length = int.from_bytes(raw[8:12], "little")
        msg_num = int.from_bytes(raw[14:16], "little")
        if msg_num != self._live_msg_num:
            return None

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
        frame = self._live_frame(self._data)
        if frame is None:
            super().parse_bc_data()
            return

        trace = self._live_trace
        if trace is not None and frame.cmd_id == LIVE_START_CMD_ID:
            trace.cmd3_frames += 1
            trace.total_body_bytes += frame.body_length
            if frame.body:
                trace.body_frames += 1
            if trace.first_cmd3_delay_ms is None and self._live_started_at is not None:
                trace.first_cmd3_delay_ms = round(
                    max(0.0, self._loop.time() - self._live_started_at) * 1000.0,
                    3,
                )

        if self._live_observer is not None:
            self._live_observer(frame)

        future = (
            self._live_start_future
            if frame.cmd_id == LIVE_START_CMD_ID
            else self._live_stop_future
        )
        if future is not None and not future.done():
            future.set_result(frame)

        # cmd3/cmd4 use channel + stream + 16-bit msgNum. Do not let the stock
        # parser reinterpret bytes 12..15 as its generic 24-bit message id.
        self._data = b""

    def connection_lost(self, exc: Exception | None = None) -> None:
        trace = self._live_trace
        if trace is not None:
            trace.connection_lost_exception_present = exc is not None
        for future in (self._live_start_future, self._live_stop_future):
            if future is not None and not future.done():
                future.set_exception(
                    exc or ConnectionError("live stream connection closed")
                )
        super().connection_lost(exc)


class _LiveStreamConnection(BoundBaichuanUdpConnection):
    """Private PoC transport with cmd3 observation and post-login heartbeat."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._live_decryptor: Callable[..., bytes | str] | None = None
        self._live_trace = LiveStreamTrace(attempted=True)
        self._heartbeat_task: asyncio.Task[None] | None = None

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

    def prepare_live_probe(
        self,
        decryptor: Callable[..., bytes | str],
        *,
        stream_kind: StreamKind,
    ) -> LiveStreamTrace:
        self._live_decryptor = decryptor
        self._live_trace = LiveStreamTrace(
            attempted=True, stream_kind=stream_kind
        )
        return self._live_trace

    def _try_aes(self, data: bytes, header: bytes) -> bytes | None:
        if not data or self._live_decryptor is None:
            return None
        try:
            decoded = self._live_decryptor(data, header, decode=False)
        except Exception:
            return None
        return decoded if isinstance(decoded, bytes) else None

    def _observe_live_frame(self, frame: _RawLiveFrame) -> None:
        if frame.cmd_id != LIVE_START_CMD_ID or not frame.body:
            return

        body = frame.body
        candidates: list[bytes] = []
        if frame.payload_offset > 0:
            enc_extension = body[: frame.payload_offset]
            payload = body[frame.payload_offset :]
            extension = self._try_aes(enc_extension, frame.header) or b""
            encrypt_len = _extension_encrypt_len(extension)
            if encrypt_len and payload:
                encrypt_len = min(encrypt_len, len(payload))
                prefix = self._try_aes(payload[:encrypt_len], frame.header)
                if prefix is not None:
                    candidates.append(prefix + payload[encrypt_len:])
            if payload:
                candidates.append(payload)
                decoded = self._try_aes(payload, frame.header)
                if decoded is not None:
                    candidates.append(decoded)
        else:
            decoded = self._try_aes(body, frame.header)
            if decoded is not None:
                candidates.append(decoded)
            candidates.append(body)

        for candidate in candidates:
            if _scan_bcmedia(candidate, self._live_trace):
                return
        self._live_trace.unknown_body_frames += 1

    def _send_heartbeat(self) -> bool:
        protocol = self._protocol
        transport = self._transport
        if (
            not isinstance(protocol, _LiveStreamProtocol)
            or transport is None
            or protocol.client_id is None
            or protocol.host_id is None
        ):
            return False

        transaction_id = secrets.randbelow(999_000) + 1_000
        packet = _encode_p2p_heartbeat(
            transaction_id,
            protocol.client_id,
            protocol.host_id,
        )
        transport.sendto(packet, (self._host, self._port))
        self._live_trace.heartbeat_count += 1
        return True

    async def _heartbeat_loop(self) -> None:
        try:
            while self.connection_open:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                if not self.connection_open:
                    break
                self._send_heartbeat()
        except asyncio.CancelledError:
            raise

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._send_heartbeat()
        self._heartbeat_task = self._loop.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def send_live_stream_probe(
        self,
        start_wire: bytes,
        stop_wire: bytes,
        *,
        msg_num: int,
        duration: float,
    ) -> LiveStreamTrace:
        protocol = self._protocol
        if not isinstance(protocol, _LiveStreamProtocol):
            raise RuntimeError("unexpected live-stream UDP protocol")

        trace = self._live_trace
        trace.start_attempted = True
        start_future, stop_future = protocol.arm_live_probe(
            msg_num,
            trace,
            self._observe_live_frame,
        )
        started_at = self._loop.time()

        try:
            await self.send_without_wait(
                start_wire,
                cmd_id=LIVE_START_CMD_ID,
                timeout=5,
            )
            start_frame = await asyncio.wait_for(
                asyncio.shield(start_future),
                timeout=START_TIMEOUT_SECONDS,
            )
            trace.start_response_code = start_frame.response_code
            trace.start_accepted = (
                start_frame.response_code in ACCEPTED_RESPONSE_CODES
            )
            if not trace.start_accepted:
                trace.termination_reason = (
                    f"start_response_{start_frame.response_code}"
                )
                return trace

            deadline = self._loop.time() + max(
                0.1,
                min(float(duration), MAX_PROBE_SECONDS),
            )
            while self.connection_open and self._loop.time() < deadline:
                await asyncio.sleep(0.05)

            trace.termination_reason = (
                "duration_reached"
                if self.connection_open
                else "connection_closed"
            )

            if self.connection_open:
                trace.stop_attempted = True
                try:
                    await self.send_without_wait(
                        stop_wire,
                        cmd_id=LIVE_STOP_CMD_ID,
                        timeout=2,
                    )
                    stop_frame = await asyncio.wait_for(
                        asyncio.shield(stop_future),
                        timeout=STOP_TIMEOUT_SECONDS,
                    )
                    trace.stop_response_code = stop_frame.response_code
                    trace.stop_accepted = (
                        stop_frame.response_code in ACCEPTED_RESPONSE_CODES
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    trace.termination_reason = "stop_timeout"

            return trace
        finally:
            trace.elapsed_seconds = round(
                self._loop.time() - started_at,
                3,
            )
            protocol.clear_live_probe()
            self._live_decryptor = None

    async def close(self) -> None:
        await self.stop_heartbeat()
        await super().close()


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
    """Wake, login, sample Preview, stop it, and always close the camera."""
    lease = None
    host = None
    connection: _LiveStreamConnection | None = None
    failure_stage = "UID_RESOLVE_ERROR"
    uid_trace = UidResolveTrace(timeout_seconds=float(resolve_timeout))
    trace = LiveStreamTrace(attempted=True, stream_kind=stream)

    try:
        interface_name, _ = await asyncio.to_thread(
            linux_ipv4_interface,
            str(interface.ip),
        )
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
            raise RuntimeError("single lease handoff was not adopted")
        lease = None

        failure_stage = "LIVE_AUTH_ERROR"
        host.baichuan._first_login = False
        await host.baichuan.login()

        trace = connection.prepare_live_probe(
            host.baichuan._aes_decrypt,
            stream_kind=stream,
        )
        connection.start_heartbeat()

        start_wire, start_request = _build_preview_wire(
            host.baichuan,
            cmd_id=LIVE_START_CMD_ID,
            stream=stream,
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
    except (
        ReolinkError,
        OSError,
        TimeoutError,
        asyncio.TimeoutError,
        RuntimeError,
        ValueError,
    ) as err:
        rsp_code = getattr(err, "rspCode", None)
        raise LiveStreamProbeError(
            failure_stage,
            failure_type=type(err).__name__,
            response_code=rsp_code if isinstance(rsp_code, int) else None,
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
