"""Private UID/LAN transport for the RTSP PoC.

This is intentionally local to the PoC domain so loading it cannot import or
patch the production Reolink Battery integration.
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import socket
import struct
import sys
import time
from dataclasses import dataclass
from xml.etree import ElementTree as XML

from reolink_aio.baichuan import xmls
from reolink_aio.baichuan.base_protocol import BaichuanBaseConnection
from reolink_aio.baichuan.udp_protocol import BaichuanUdpClientProtocol, BaichuanUdpConnection
from reolink_aio.baichuan.util import calc_crc, decrypt_udp_baichuan, encrypt_udp_baichuan
from reolink_aio.exceptions import ReolinkConnectionError

DISCOVERY_PORTS = (2018, 2015)
DISCOVERY_MAGIC = bytes.fromhex("3acf872a")
BAICHUAN_MAGIC = bytes.fromhex("f0debc0a")
UID_RESOLVE_TIMEOUT_SECONDS = 15.0
UID_RESOLVE_RESEND_SECONDS = 0.5


def linux_ipv4_interface(source_ip: str) -> tuple[str, int]:
    if not sys.platform.startswith("linux"):
        raise OSError("RTSP PoC runtime requires Linux")
    import fcntl

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for interface_index, interface_name in socket.if_nameindex():
            request = struct.pack("256s", interface_name.encode("ascii")[:15])
            try:
                response = fcntl.ioctl(sock.fileno(), 0x8915, request)
            except OSError:
                continue
            if socket.inet_ntoa(response[20:24]) == source_ip:
                return interface_name, interface_index
    raise OSError(f"no Linux network interface owns source IP {source_ip}")


def _linux_route_interface(destination: str) -> str:
    with open("/proc/net/route", encoding="ascii") as routes:
        route_table = routes.read()
    target = int(ipaddress.IPv4Address(destination))
    best: tuple[int, int, str] | None = None
    for line in route_table.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            network = int.from_bytes(bytes.fromhex(fields[1]), "little")
            flags = int(fields[3], 16)
            metric = int(fields[6])
            mask = int.from_bytes(bytes.fromhex(fields[7]), "little")
        except ValueError:
            continue
        if flags & 1 and target & mask == network & mask:
            candidate = (mask.bit_count(), -metric, fields[0])
            if best is None or candidate > best:
                best = candidate
    if best is None:
        raise OSError(f"Linux has no route to {destination}")
    return best[2]


def validate_local_lan_route(
    interface: ipaddress.IPv4Interface, camera_ip: str, interface_name: str
) -> None:
    camera = ipaddress.IPv4Address(camera_ip)
    if camera not in interface.network:
        raise OSError(f"resolved camera IP {camera} is outside {interface.network}")
    route_interface = _linux_route_interface(camera_ip)
    if route_interface != interface_name:
        raise OSError(
            f"Linux routes camera IP through {route_interface}, not {interface_name}"
        )


def _packet(body: str) -> bytes:
    transaction_id = secrets.randbelow(999_000) + 1_000
    payload = encrypt_udp_baichuan(body, transaction_id)
    return (
        DISCOVERY_MAGIC
        + len(payload).to_bytes(4, "little")
        + bytes.fromhex("01000000")
        + transaction_id.to_bytes(4, "little")
        + calc_crc(payload)
        + payload
    )


def _broadcast_targets(
    interface: ipaddress.IPv4Interface,
) -> tuple[tuple[str, int], ...]:
    addresses = {"255.255.255.255", str(interface.network.broadcast_address)}
    return tuple(
        (address, port)
        for address in sorted(addresses)
        for port in DISCOVERY_PORTS
    )


def _parse_reply(data: bytes, expected_client_id: int) -> int | None:
    if len(data) < 20 or data[:4] != DISCOVERY_MAGIC:
        return None
    payload_length = int.from_bytes(data[4:8], "little")
    if len(data) < 20 + payload_length:
        return None
    transaction_id = int.from_bytes(data[12:16], "little")
    payload = data[20 : 20 + payload_length]
    if calc_crc(payload) != data[16:20]:
        return None
    try:
        root = XML.fromstring(decrypt_udp_baichuan(payload, transaction_id))
        reply = root.find("D2C_C_R")
        if reply is None or reply.findtext("cid") != str(expected_client_id):
            return None
        return int(reply.findtext("did", ""))
    except (UnicodeError, ValueError, XML.ParseError):
        return None


@dataclass(slots=True)
class UidResolveTrace:
    timeout_seconds: float = UID_RESOLVE_TIMEOUT_SECONDS
    resend_interval_seconds: float = UID_RESOLVE_RESEND_SECONDS
    send_rounds: int = 0
    datagrams_sent: int = 0
    elapsed_ms: float | None = None
    succeeded: bool = False


@dataclass(slots=True)
class UidLanLease:
    host: str
    port: int
    source_ip: str
    interface_index: int
    client_id: int
    device_id: int
    transaction_id: int
    socket: socket.socket | None

    def detach_socket(self) -> socket.socket:
        sock = self.socket
        if sock is None or sock.fileno() < 0:
            raise RuntimeError("UID LAN lease socket is not available")
        self.socket = None
        return sock

    def close(self) -> None:
        sock = self.socket
        if sock is None or sock.fileno() < 0:
            return
        body = xmls.UDP_DISCONNECT_XML.format(
            client_id=self.client_id, host_id=self.device_id
        )
        try:
            sock.sendto(_packet(body), (self.host, self.port))
        finally:
            sock.close()
            self.socket = None


def resolve_uid_lan(
    uid: str,
    interface: ipaddress.IPv4Interface,
    timeout: float = UID_RESOLVE_TIMEOUT_SECONDS,
    trace: UidResolveTrace | None = None,
) -> UidLanLease:
    if not uid.isalnum() or len(uid) > 127:
        raise ValueError("UID must contain 1-127 alphanumeric characters")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    started_at = time.monotonic()
    if trace is not None:
        trace.timeout_seconds = float(timeout)
        trace.resend_interval_seconds = UID_RESOLVE_RESEND_SECONDS
        trace.send_rounds = 0
        trace.datagrams_sent = 0
        trace.elapsed_ms = None
        trace.succeeded = False

    _, interface_index = linux_ipv4_interface(str(interface.ip))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for local_port in secrets.SystemRandom().sample(range(53500, 54000), 500):
        try:
            sock.bind((str(interface.ip), local_port))
            break
        except OSError:
            continue
    else:
        sock.close()
        raise OSError("no UDP discovery port available in 53500-53999")

    sock.settimeout(0.15)
    client_id = secrets.randbelow(2_147_483_646) + 1
    body = xmls.UDP_CONNECT_XML.format(
        uid=uid,
        port=sock.getsockname()[1],
        client_id=client_id,
        mtu=1350,
    ).replace("<p>WIN</p>", "<p>MAC</p>")
    packet = _packet(body)
    transaction_id = int.from_bytes(packet[12:16], "little")
    targets = _broadcast_targets(interface)
    deadline = time.monotonic() + timeout
    next_send = 0.0

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                for target in targets:
                    sock.sendto(packet, target)
                if trace is not None:
                    trace.send_rounds += 1
                    trace.datagrams_sent += len(targets)
                next_send = now + UID_RESOLVE_RESEND_SECONDS
            try:
                data, address = sock.recvfrom(65535)
            except TimeoutError:
                continue
            device_id = _parse_reply(data, client_id)
            if device_id is None:
                continue
            if trace is not None:
                trace.elapsed_ms = round((time.monotonic() - started_at) * 1000.0, 3)
                trace.succeeded = True
            return UidLanLease(
                address[0],
                address[1],
                str(interface.ip),
                interface_index,
                client_id,
                device_id,
                transaction_id,
                sock,
            )
    except BaseException:
        if trace is not None and trace.elapsed_ms is None:
            trace.elapsed_ms = round((time.monotonic() - started_at) * 1000.0, 3)
        sock.close()
        raise

    if trace is not None:
        trace.elapsed_ms = round((time.monotonic() - started_at) * 1000.0, 3)
    sock.close()
    raise TimeoutError(f"UID LAN resolution timed out after {timeout:.1f} seconds")


class _IdempotentUdpClientProtocol(BaichuanUdpClientProtocol):
    """Only make connection close idempotent; no production stream patches."""

    def connection_lost(self, exc: Exception | None = None) -> None:
        if not self.close_future.done():
            super().connection_lost(exc)


class BoundBaichuanUdpConnection(BaichuanUdpConnection):
    """Baichuan UDP connection pinned to the selected source IPv4."""

    def __init__(
        self,
        host: str,
        source_ip: str,
        *args,
        handoff_lease: UidLanLease | None = None,
        **kwargs,
    ) -> None:
        super().__init__(host, *args, **kwargs)
        self.source_ip = source_ip
        self._handoff_mode = handoff_lease is not None
        self._handoff_lease = handoff_lease
        self._handoff_active = False

    def _take_handoff_socket(self) -> tuple[socket.socket, UidLanLease] | None:
        lease = self._handoff_lease
        if lease is None:
            return None
        if lease.host != self._host or lease.source_ip != self.source_ip:
            raise OSError("UID LAN lease does not match requested Baichuan endpoint")
        sock = lease.detach_socket()
        self._handoff_lease = None
        self._handoff_active = True
        return sock, lease

    def _apply_handoff_protocol(
        self, protocol: BaichuanUdpClientProtocol, lease: UidLanLease
    ) -> None:
        protocol.client_id = lease.client_id
        protocol.host_id = lease.device_id
        protocol.remote_port = lease.port
        self._port = lease.port

    async def connect(self):
        if not self._handoff_mode:
            await super().connect()
            return
        if self.connection_open:
            return
        if self._handoff_lease is None:
            raise ReolinkConnectionError(
                f"Baichuan host {self._host}: single lease session cannot be reopened"
            )
        await BaichuanBaseConnection.connect(self)
        if not self.connection_open:
            raise ReolinkConnectionError(
                f"Baichuan host {self._host}: single lease handoff did not open"
            )

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
                lambda: _IdempotentUdpClientProtocol(
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

    async def close(self) -> None:
        protocol = self._protocol
        try:
            if (
                protocol is not None
                and protocol.client_id is not None
                and protocol.host_id is not None
            ):
                body = xmls.UDP_DISCONNECT_XML.format(
                    client_id=protocol.client_id, host_id=protocol.host_id
                )
                message, _ = self._construct_udp_mess(body)
                await BaichuanBaseConnection.send_without_wait(
                    self, message, timeout=5
                )
        finally:
            if protocol is not None and not protocol.close_future.done():
                protocol.connection_lost()
            await super().close()
