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
    proactive_cmd234_count: int = 0
    remote_disconnect_observed: bool = False
    connection_lost_exception_present: bool = False


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
        super().__init__(*args, **kwargs)
        self.remote_disconnect_observed = False
        self.connection_lost_exception_present = False

    def parse_udp_connection(self, port: int) -> None:
        if _packet_is_remote_disconnect(self._udp_data):
            self.remote_disconnect_observed = True
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
        next_p2p_heartbeat_at = started_at
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
                cmd_id=8,
                timeout=min(max(float(timeout), 1.0), 5.0),
            )

            while True:
                now = self._loop.time()
                if now >= next_p2p_heartbeat_at:
                    trace.p2p_heartbeat_attempted = True
                    if self._send_p2p_heartbeat():
                        trace.p2p_heartbeat_count += 1
                    next_p2p_heartbeat_at = now + P2P_HEARTBEAT_INTERVAL

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
