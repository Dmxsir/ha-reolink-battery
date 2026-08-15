"""Beta.20 full-transfer diagnostic with transport-level P2P heartbeat.

This keeps the physically validated cmd13 -> cmd8 sequence and beta.19 chunk
assembly unchanged, removes the proactive Baichuan cmd234 keepalive, and adds
the UID/P2P transport heartbeat used to keep the reliable UDP channel alive.
No recording file is written and no cmd9 is sent in this beta.
"""

from __future__ import annotations

import asyncio
import copy
import secrets
import socket
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from reolink_aio.baichuan.udp_protocol import MTU
from reolink_aio.baichuan.util import (
    calc_crc,
    decrypt_udp_baichuan,
    encrypt_udp_baichuan,
)

from . import recording_download_probe as base
from . import recording_download_probe_beta17 as beta17
from . import recording_download_probe_beta19 as beta19
from .recording_probe import RecordingCandidate

CONTENT_LAYOUT = "cmd13_prepare_cmd8_p2p_heartbeat_full_transfer_shape"
P2P_HEARTBEAT_INTERVAL = 1.0
RELIABLE_UDP_ACK_TIMEOUT = 0.5
RELIABLE_UDP_MAX_RETRANSMITS = 2
STREAM_IDLE_TIMEOUT = 30.0
STREAM_HARD_TIMEOUT = 240.0
STREAM_SAMPLE_MAX_BYTES = 16 * 1024 * 1024
STREAM_SAMPLE_MAX_FRAMES = 4096


@dataclass(slots=True)
class P2PHeartbeatProbeTrace(beta19.FullTransferProbeTrace):
    """Secret-safe telemetry for transport-heartbeat full-transfer testing."""

    p2p_heartbeat_attempted: bool = False
    p2p_heartbeat_count: int = 0
    p2p_heartbeat_interval_seconds: float = P2P_HEARTBEAT_INTERVAL
    p2p_heartbeat_started_after_handoff: bool = False
    p2p_heartbeat_first_delay_seconds: float | None = None
    p2p_heartbeat_pre_cmd13_count: int = 0
    p2p_heartbeat_background_task_active: bool = False
    proactive_cmd234_count: int = 0
    remote_disconnect_observed: bool = False
    connection_lost_exception_present: bool = False
    cmd13_udp_seq: int | None = None
    cmd13_udp_ack_received: bool = False
    cmd13_udp_ack_delay_ms: float | None = None
    cmd13_udp_retransmit_count: int = 0
    cmd8_udp_seq: int | None = None
    cmd8_udp_ack_received: bool = False
    cmd8_udp_ack_delay_ms: float | None = None
    cmd8_udp_retransmit_count: int = 0
    udp_bc_packets_received: int = 0
    udp_seq_gap_events: int = 0
    udp_missing_packet_count: int = 0
    udp_out_of_order_packets: int = 0
    udp_duplicate_packets: int = 0
    udp_reorder_buffer_peak: int = 0
    udp_ack_sent_count: int = 0
    udp_ack_with_gap_bitmap_count: int = 0
    udp_last_contiguous_seq: int | None = None
    udp_max_ack_delay_ms: float | None = None
    udp_seq_at_remote_disconnect: int | None = None
    udp_snapshot_from_local_protocol: bool = False


@dataclass(frozen=True, slots=True)
class DownloadP2PHeartbeatProbeResult:
    """Base prepare result plus bounded beta.20 transfer telemetry."""

    prepare: base.DownloadPrepareResult
    stream_trace: P2PHeartbeatProbeTrace

    def __getattr__(self, name: str) -> Any:
        return getattr(self.prepare, name)


_STREAM_STATES: dict[str, P2PHeartbeatProbeTrace] = {}
_RESULT_TRACE: ContextVar[P2PHeartbeatProbeTrace | None] = ContextVar(
    "reolink_battery_beta20_p2p_heartbeat_trace", default=None
)


def _new_trace(*, attempted: bool = False) -> P2PHeartbeatProbeTrace:
    return P2PHeartbeatProbeTrace(
        attempted=attempted,
        sample_limit_bytes=STREAM_SAMPLE_MAX_BYTES,
        sample_limit_frames=STREAM_SAMPLE_MAX_FRAMES,
        aggregate_limit_bytes=STREAM_SAMPLE_MAX_BYTES,
        keepalive_attempted=False,
        keepalive_count=0,
        keepalive_interval_seconds=0.0,
    )


def _clone_trace(trace: P2PHeartbeatProbeTrace) -> P2PHeartbeatProbeTrace:
    return copy.deepcopy(trace)


def stream_probe_state(entry_id: str) -> P2PHeartbeatProbeTrace:
    """Return persistent secret-safe beta.20 telemetry."""
    return _STREAM_STATES.setdefault(entry_id, _new_trace())


def reset_stream_probe_state(entry_id: str) -> None:
    """Reset beta.20 diagnostics before one explicit manual attempt."""
    _STREAM_STATES[entry_id] = _new_trace(attempted=True)


def apply_stream_probe_trace(
    entry_id: str, trace: P2PHeartbeatProbeTrace | None
) -> None:
    """Persist only secret-safe beta.20 telemetry."""
    if trace is not None:
        _STREAM_STATES[entry_id] = _clone_trace(trace)


def _encode_p2p_heartbeat(
    transaction_id: int,
    client_id: int,
    device_id: int,
) -> bytes:
    """Build one C2D_HB discovery-layer heartbeat packet."""
    xml = (
        "<P2P><C2D_HB>"
        f"<cid>{client_id}</cid><did>{device_id}</did>"
        "</C2D_HB></P2P>"
    )
    payload = encrypt_udp_baichuan(xml, transaction_id)
    return (
        beta17.transport_mod.DISCOVERY_MAGIC
        + len(payload).to_bytes(4, "little")
        + bytes.fromhex("01000000")
        + transaction_id.to_bytes(4, "little")
        + calc_crc(payload)
        + payload
    )


def _packet_is_remote_disconnect(data: bytes) -> bool:
    """Return True only for a valid discovery packet carrying D2C_DISC."""
    if len(data) < 20 or data[:4] != beta17.transport_mod.DISCOVERY_MAGIC:
        return False
    payload_length = int.from_bytes(data[4:8], "little")
    if len(data) < 20 + payload_length:
        return False
    transaction_id = int.from_bytes(data[12:16], "little")
    payload = data[20 : 20 + payload_length]
    if calc_crc(payload) != data[16:20]:
        return False
    try:
        decoded = decrypt_udp_baichuan(payload, transaction_id)
    except Exception:
        return False
    if isinstance(decoded, bytes):
        return b"<D2C_DISC" in decoded
    return "<D2C_DISC" in str(decoded)


class _P2PHeartbeatProbeProtocol(beta17._StreamProbeProtocol):
    """Stream observer with secret-safe remote-disconnect classification."""

    def __init__(self, *args, **kwargs) -> None:
        self._reliable_ack_callback = kwargs.pop("reliable_ack_callback", None)
        super().__init__(*args, **kwargs)
        self.remote_disconnect_observed = False
        self.connection_lost_exception_present = False
        self.udp_bc_packets_received = 0
        self.udp_seq_gap_events = 0
        self.udp_missing_packet_count = 0
        self.udp_out_of_order_packets = 0
        self.udp_duplicate_packets = 0
        self.udp_reorder_buffer_peak = 0
        self.udp_ack_sent_count = 0
        self.udp_ack_with_gap_bitmap_count = 0
        self.udp_last_contiguous_seq: int | None = None
        self.udp_seq_at_remote_disconnect: int | None = None
        self._missing_seq_ids_seen: set[int] = set()
        self._cmd8_delivery_future: asyncio.Future[bool] | None = None


    def arm_cmd8_delivery_future(self) -> asyncio.Future[bool]:
        """Return a future completed by the first accepted cmd8 stream frame."""
        future = self._cmd8_delivery_future
        if future is not None and not future.done():
            raise RuntimeError("cmd8 delivery future already armed")
        self._cmd8_delivery_future = self._loop.create_future()
        return self._cmd8_delivery_future

    def clear_stream_probe(self) -> None:
        future = self._cmd8_delivery_future
        if future is not None and not future.done():
            future.cancel()
        self._cmd8_delivery_future = None
        super().clear_stream_probe()

    def _observe_stream_frame(self, raw: bytes) -> None:
        trace = self._stream_trace
        before = trace.cmd8_frames if trace is not None else 0
        super()._observe_stream_frame(raw)
        trace = self._stream_trace
        future = self._cmd8_delivery_future
        if (
            trace is not None
            and trace.cmd8_frames > before
            and future is not None
            and not future.done()
        ):
            future.set_result(True)

    def parse_udp_ack(self, port: int) -> None:
        data = self._udp_data
        if len(data) >= 28:
            payload_size = int.from_bytes(data[24:28], "little")
            message_length = 28 + payload_size
            if len(data) >= message_length:
                client_id = int.from_bytes(data[4:8], "little")
                if client_id == self.client_id:
                    base_seq = int.from_bytes(data[16:20], "little")
                    payload = data[28:message_length]
                    callback = self._reliable_ack_callback
                    if callback is not None:
                        callback(base_seq, payload)
        super().parse_udp_ack(port)

    def parse_udp_bc(self, port: int) -> None:
        data = self._udp_data
        if len(data) >= 20:
            client_id = int.from_bytes(data[4:8], "little")
            seq_id = int.from_bytes(data[12:16], "little")
            if client_id == self.client_id:
                self.udp_bc_packets_received += 1
                expected = self._recv_seq_id + 1
                if seq_id <= self._recv_seq_id:
                    self.udp_duplicate_packets += 1
                elif seq_id > expected:
                    self.udp_seq_gap_events += 1
                    self.udp_out_of_order_packets += 1
                    missing = set(range(expected, seq_id))
                    newly_seen = missing - self._missing_seq_ids_seen
                    self.udp_missing_packet_count += len(newly_seen)
                    self._missing_seq_ids_seen.update(newly_seen)
        try:
            super().parse_udp_bc(port)
        finally:
            self.udp_reorder_buffer_peak = max(
                self.udp_reorder_buffer_peak, len(self._seq_data)
            )
            self.udp_last_contiguous_seq = (
                self._recv_seq_id if self._recv_seq_id >= 0 else None
            )

    def send_ack(self) -> None:
        will_send = (
            self._transport is not None
            and self.host_id is not None
            and self._recv_seq_id >= 0
        )
        gap_bitmap = False
        if will_send and self._seq_data:
            highest = max(self._seq_data)
            gap_bitmap = any(
                seq_id not in self._seq_data
                for seq_id in range(self._recv_seq_id + 1, highest)
            )
        super().send_ack()
        if will_send:
            self.udp_ack_sent_count += 1
            if gap_bitmap:
                self.udp_ack_with_gap_bitmap_count += 1

    def parse_udp_connection(self, port: int) -> None:
        if _packet_is_remote_disconnect(self._udp_data):
            self.remote_disconnect_observed = True
            self.udp_seq_at_remote_disconnect = (
                self._recv_seq_id if self._recv_seq_id >= 0 else None
            )
        super().parse_udp_connection(port)

    def connection_lost(self, exc: Exception | None = None) -> None:
        self.connection_lost_exception_present = exc is not None
        super().connection_lost(exc)


class _P2PHeartbeatFullTransferConnection(beta19._FullTransferProbeConnection):
    """Same authenticated UDP session with discovery-layer C2D_HB heartbeat."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_trace = _new_trace(attempted=True)
        self._p2p_heartbeat_tid: int | None = None
        self._p2p_heartbeat_task: asyncio.Task[None] | None = None
        self._p2p_heartbeat_started_at: float | None = None
        self._p2p_heartbeat_first_sent_at: float | None = None
        self._p2p_heartbeat_total_count = 0
        self._p2p_heartbeat_started_after_handoff = False
        self._reliable_ack_waiters: dict[int, asyncio.Future[bool]] = {}
        self._reliable_sent_at: dict[int, float] = {}
        self._reliable_ack_delays_ms: dict[int, float] = {}
        self._reliable_acked_seq_ids: set[int] = set()
        self._reliable_command_seq: dict[int, int] = {}
        self._reliable_command_retransmits: dict[int, int] = {}

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
                lambda: _P2PHeartbeatProbeProtocol(
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


    def _observe_udp_ack(self, base_seq: int, payload: bytes) -> None:
        """Resolve tracked command delivery from cumulative/bitmap UDP ACKs."""
        now = self._loop.time()
        for seq_id, sent_at in tuple(self._reliable_sent_at.items()):
            acknowledged = seq_id <= base_seq
            if not acknowledged and seq_id > base_seq:
                offset = seq_id - base_seq - 1
                acknowledged = offset < len(payload) and payload[offset] != 0
            if not acknowledged:
                continue
            if seq_id not in self._reliable_acked_seq_ids:
                self._reliable_acked_seq_ids.add(seq_id)
                self._reliable_ack_delays_ms[seq_id] = round(
                    max(0.0, now - sent_at) * 1000.0, 3
                )
            future = self._reliable_ack_waiters.get(seq_id)
            if future is not None and not future.done():
                future.set_result(True)

    async def _send_reliable_download_packet(
        self,
        data: bytes,
        *,
        cmd_id: int,
        response_future: asyncio.Future[Any] | None = None,
        ack_timeout: float = RELIABLE_UDP_ACK_TIMEOUT,
        max_retransmits: int = RELIABLE_UDP_MAX_RETRANSMITS,
    ) -> int:
        """Send one BC command with bounded same-sequence UDP retransmission."""
        if not self.connection_open:
            await self.connect()
        transport = self._transport
        if transport is None:
            raise ConnectionError("reliable UDP transport is not open")

        async with self._mutex:
            udp_header = await self._construct_udp_header(len(data))
            packet = udp_header + data
            if len(packet) > MTU - 20:
                raise RuntimeError("reliable download command unexpectedly requires UDP fragmentation")
            seq_id = int.from_bytes(udp_header[12:16], "little")
            ack_future: asyncio.Future[bool] = self._loop.create_future()
            self._reliable_ack_waiters[seq_id] = ack_future
            self._reliable_sent_at[seq_id] = self._loop.time()
            self._reliable_command_seq[cmd_id] = seq_id
            transport.sendto(packet, (self._host, self._port))

        retransmits = 0
        try:
            while True:
                waiters: list[asyncio.Future[Any]] = [ack_future]
                if response_future is not None:
                    waiters.append(response_future)
                await asyncio.wait(
                    waiters,
                    timeout=max(float(ack_timeout), 0.05),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if ack_future.done() or (
                    response_future is not None and response_future.done()
                ):
                    break
                if retransmits >= max_retransmits or not self.connection_open:
                    break
                retransmits += 1
                # Retransmit the exact same UDP packet: same BC command and seq_id.
                transport.sendto(packet, (self._host, self._port))
        finally:
            self._reliable_command_retransmits[cmd_id] = retransmits
            self._reliable_ack_waiters.pop(seq_id, None)
        return seq_id

    def _apply_udp_reliability_trace(
        self,
        trace: P2PHeartbeatProbeTrace,
        *,
        protocol: _P2PHeartbeatProbeProtocol | None = None,
    ) -> None:
        """Copy command ACK and receive-gap telemetry into the public trace.

        An explicit local protocol reference preserves RX counters after a remote
        D2C_DISC closes the connection and clears self._protocol.
        """
        cmd13_seq = self._reliable_command_seq.get(13)
        cmd8_seq = self._reliable_command_seq.get(8)
        trace.cmd13_udp_seq = cmd13_seq
        trace.cmd13_udp_ack_received = (
            cmd13_seq is not None and cmd13_seq in self._reliable_acked_seq_ids
        )
        trace.cmd13_udp_ack_delay_ms = (
            self._reliable_ack_delays_ms.get(cmd13_seq)
            if cmd13_seq is not None
            else None
        )
        trace.cmd13_udp_retransmit_count = self._reliable_command_retransmits.get(13, 0)
        trace.cmd8_udp_seq = cmd8_seq
        trace.cmd8_udp_ack_received = (
            cmd8_seq is not None and cmd8_seq in self._reliable_acked_seq_ids
        )
        trace.cmd8_udp_ack_delay_ms = (
            self._reliable_ack_delays_ms.get(cmd8_seq)
            if cmd8_seq is not None
            else None
        )
        trace.cmd8_udp_retransmit_count = self._reliable_command_retransmits.get(8, 0)
        delays = list(self._reliable_ack_delays_ms.values())
        trace.udp_max_ack_delay_ms = max(delays) if delays else None
        snapshot_protocol = protocol
        if snapshot_protocol is None:
            current_protocol = self._protocol
            if isinstance(current_protocol, _P2PHeartbeatProbeProtocol):
                snapshot_protocol = current_protocol
        trace.udp_snapshot_from_local_protocol = protocol is not None
        if isinstance(snapshot_protocol, _P2PHeartbeatProbeProtocol):
            trace.udp_bc_packets_received = snapshot_protocol.udp_bc_packets_received
            trace.udp_seq_gap_events = snapshot_protocol.udp_seq_gap_events
            trace.udp_missing_packet_count = snapshot_protocol.udp_missing_packet_count
            trace.udp_out_of_order_packets = snapshot_protocol.udp_out_of_order_packets
            trace.udp_duplicate_packets = snapshot_protocol.udp_duplicate_packets
            trace.udp_reorder_buffer_peak = snapshot_protocol.udp_reorder_buffer_peak
            trace.udp_ack_sent_count = snapshot_protocol.udp_ack_sent_count
            trace.udp_ack_with_gap_bitmap_count = snapshot_protocol.udp_ack_with_gap_bitmap_count
            trace.udp_last_contiguous_seq = snapshot_protocol.udp_last_contiguous_seq
            trace.udp_seq_at_remote_disconnect = snapshot_protocol.udp_seq_at_remote_disconnect

    def _construct_udp_mess(self, body: str) -> tuple[bytes, int]:
        packet, transaction_id = super()._construct_udp_mess(body)
        if "<C2D_C>" in body:
            self._p2p_heartbeat_tid = transaction_id
        return packet, transaction_id

    def prepare_cmd8_probe(
        self,
        decryptor,
        cmd8_wire: bytes,
        cmd8_msg_num: int,
        cmd8_body_length: int,
        cmd8_payload_offset: int,
    ) -> None:
        self._stream_decryptor = decryptor
        self._stream_trace = _new_trace(attempted=True)
        self._stream_trace.cmd8_request_msg_num = cmd8_msg_num
        self._stream_trace.cmd8_request_body_length = cmd8_body_length
        self._stream_trace.cmd8_request_payload_offset = cmd8_payload_offset
        self._cmd8_wire = cmd8_wire
        self._cmd8_msg_num = cmd8_msg_num
        self._aggregate = bytearray()

    def _send_p2p_heartbeat(self) -> bool:
        protocol = self._protocol
        transport = self._transport
        if (
            not isinstance(protocol, _P2PHeartbeatProbeProtocol)
            or transport is None
            or protocol.client_id is None
            or protocol.host_id is None
        ):
            return False
        transaction_id = self._p2p_heartbeat_tid
        if transaction_id is None:
            transaction_id = secrets.randbelow(999_000) + 1_000
            self._p2p_heartbeat_tid = transaction_id
        packet = _encode_p2p_heartbeat(
            transaction_id,
            protocol.client_id,
            protocol.host_id,
        )
        transport.sendto(packet, (self._host, self._port))
        return True

    def _record_p2p_heartbeat(self) -> bool:
        """Send one heartbeat and update connection-lifetime counters."""
        if not self._send_p2p_heartbeat():
            return False
        now = self._loop.time()
        self._p2p_heartbeat_total_count += 1
        if self._p2p_heartbeat_first_sent_at is None:
            self._p2p_heartbeat_first_sent_at = now
        return True

    def _apply_p2p_heartbeat_trace(
        self,
        trace: P2PHeartbeatProbeTrace,
        *,
        snapshot_pre_cmd13: bool = False,
    ) -> None:
        """Copy secret-safe connection-lifetime heartbeat facts into a trace."""
        trace.p2p_heartbeat_attempted = self._p2p_heartbeat_total_count > 0
        trace.p2p_heartbeat_count = self._p2p_heartbeat_total_count
        trace.p2p_heartbeat_started_after_handoff = (
            self._p2p_heartbeat_started_after_handoff
        )
        started = self._p2p_heartbeat_started_at
        first = self._p2p_heartbeat_first_sent_at
        trace.p2p_heartbeat_first_delay_seconds = (
            round(max(0.0, first - started), 3)
            if started is not None and first is not None
            else None
        )
        task = self._p2p_heartbeat_task
        trace.p2p_heartbeat_background_task_active = bool(
            task is not None and not task.done()
        )
        if snapshot_pre_cmd13:
            trace.p2p_heartbeat_pre_cmd13_count = self._p2p_heartbeat_total_count

    async def _p2p_heartbeat_loop(self) -> None:
        """Maintain the adopted P2P lease until the Baichuan session closes."""
        try:
            while self.connection_open:
                await asyncio.sleep(P2P_HEARTBEAT_INTERVAL)
                if not self.connection_open:
                    break
                try:
                    self._record_p2p_heartbeat()
                except (OSError, RuntimeError):
                    break
        except asyncio.CancelledError:
            raise

    def _start_p2p_heartbeat_loop(self) -> None:
        """Start one immediate heartbeat plus one background sender after handoff."""
        if not getattr(self, "_handoff_active", False):
            return
        if self._p2p_heartbeat_tid is None:
            return
        task = self._p2p_heartbeat_task
        if task is not None and not task.done():
            return
        self._p2p_heartbeat_started_after_handoff = True
        self._p2p_heartbeat_started_at = self._loop.time()
        self._record_p2p_heartbeat()
        self._p2p_heartbeat_task = self._loop.create_task(
            self._p2p_heartbeat_loop()
        )

    async def _stop_p2p_heartbeat_loop(self) -> None:
        task = self._p2p_heartbeat_task
        self._p2p_heartbeat_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def connect(self):
        """Open/adopt the session and start heartbeat before login/FileInfo."""
        await super().connect()
        self._start_p2p_heartbeat_loop()

    async def close(self) -> None:
        """Stop the sole heartbeat sender before closing the adopted session."""
        await self._stop_p2p_heartbeat_loop()
        await super().close()

    async def send_file_download_probe(
        self,
        wire: bytes,
        *,
        expected_msg_num: int,
        timeout: float = 10.0,
    ) -> beta17.transport_mod.FileDownloadFrameMetadata:
        """Send cmd13 -> cmd8 and maintain only the UDP/P2P heartbeat."""
        if not self.connection_open:
            await self.connect()
        protocol = self._protocol
        if not isinstance(protocol, _P2PHeartbeatProbeProtocol):
            raise RuntimeError("unexpected beta20 Baichuan UDP protocol")
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

            trace.cmd8_attempted = True
            cmd8_delivery = protocol.arm_cmd8_delivery_future()
            await self._send_reliable_download_packet(
                self._cmd8_wire, cmd_id=8, response_future=cmd8_delivery
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
            trace.remote_disconnect_observed = protocol.remote_disconnect_observed
            trace.connection_lost_exception_present = (
                protocol.connection_lost_exception_present
            )
            self._apply_p2p_heartbeat_trace(trace)
            self._apply_udp_reliability_trace(trace)
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
    """Keep beta.19 cmd13/cmd8 bytes unchanged and attach beta.20 transport."""
    return beta19._build_cmd13_wire(baichuan, uid, candidate)


async def async_prepare_download_for_event(
    *args, **kwargs
) -> DownloadP2PHeartbeatProbeResult:
    """Run the proven transfer path with transport-level P2P heartbeat."""
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
        return DownloadP2PHeartbeatProbeResult(
            prepare=prepare,
            stream_trace=_clone_trace(trace),
        )
    finally:
        _RESULT_TRACE.reset(token)


# Preserve UID discovery, login, FileInfo search, candidate matching and
# finally-close from the proven base coroutine. Replace only the concrete
# connection and frame builder for beta.20.
base.BoundBaichuanUdpConnection = _P2PHeartbeatFullTransferConnection
base._build_cmd13_wire = _build_cmd13_wire

apply_file_info_trace = base.apply_file_info_trace
apply_identity_trace = base.apply_identity_trace
download_prepare_state = base.download_prepare_state
