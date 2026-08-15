"""Proven Linux UID/LAN transport extracted from the Phase 2 PoC."""

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
from reolink_aio.baichuan.udp_protocol import (
    BaichuanUdpClientProtocol,
    BaichuanUdpConnection,
)
from reolink_aio.baichuan.util import (
    calc_crc,
    decrypt_udp_baichuan,
    encrypt_udp_baichuan,
)

DISCOVERY_PORTS = (2018, 2015)
DISCOVERY_MAGIC = bytes.fromhex("3acf872a")
BAICHUAN_MAGIC = bytes.fromhex("f0debc0a")
FILE_DOWNLOAD_MESSAGE_CLASS = 0x6482
FILE_DOWNLOAD_CLASS_WIRE = FILE_DOWNLOAD_MESSAGE_CLASS.to_bytes(2, "little")
MODERN_24_CLASS_WIRE = (0x6414).to_bytes(2, "little")


def linux_ipv4_interface(source_ip: str) -> tuple[str, int]:
    """Return the Linux interface owning source_ip."""
    if not sys.platform.startswith("linux"):
        raise OSError("Reolink Battery runtime requires Linux")
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
    """Return Linux's longest-prefix route for destination."""
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
    """Reject a camera address routed outside the selected physical LAN."""
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
class UidLanLease:
    """Temporary UID discovery lease retained until transport is open."""

    host: str
    port: int
    source_ip: str
    interface_index: int
    client_id: int
    device_id: int
    socket: socket.socket

    def close(self) -> None:
        if self.socket.fileno() < 0:
            return
        body = xmls.UDP_DISCONNECT_XML.format(
            client_id=self.client_id, host_id=self.device_id
        )
        try:
            self.socket.sendto(_packet(body), (self.host, self.port))
        finally:
            self.socket.close()


@dataclass(frozen=True, slots=True)
class FileDownloadFrameMetadata:
    """Secret-safe metadata for the first cmd13 response frame."""

    response_code: int
    message_class: int
    channel_id: int
    stream_type: int
    msg_num: int
    body_length: int
    payload_offset: int
    payload_length: int


def resolve_uid_lan(
    uid: str,
    interface: ipaddress.IPv4Interface,
    timeout: float = 10.0,
) -> UidLanLease:
    """Resolve and wake one UID on the explicitly selected LAN."""
    if not uid.isalnum() or len(uid) > 127:
        raise ValueError("UID must contain 1-127 alphanumeric characters")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
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
    targets = _broadcast_targets(interface)
    deadline = time.monotonic() + timeout
    next_send = 0.0
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                for target in targets:
                    sock.sendto(packet, target)
                next_send = now + 0.5
            try:
                data, address = sock.recvfrom(65535)
            except TimeoutError:
                continue
            device_id = _parse_reply(data, client_id)
            if device_id is not None:
                return UidLanLease(
                    address[0],
                    address[1],
                    str(interface.ip),
                    interface_index,
                    client_id,
                    device_id,
                    sock,
                )
    except BaseException:
        sock.close()
        raise
    sock.close()
    raise TimeoutError(f"UID LAN resolution timed out after {timeout:.1f} seconds")


class _IdempotentUdpClientProtocol(BaichuanUdpClientProtocol):
    """Make close idempotent and tolerate the file-download 0x6482 class."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._file_download_probe_future: asyncio.Future[
            FileDownloadFrameMetadata
        ] | None = None
        self._patched_file_download_class = False

    def arm_file_download_probe(self) -> asyncio.Future[FileDownloadFrameMetadata]:
        """Observe the first complete cmd13 frame without retaining payload bytes."""
        if (
            self._file_download_probe_future is not None
            and not self._file_download_probe_future.done()
        ):
            raise RuntimeError("file download probe already armed")
        self._file_download_probe_future = self._loop.create_future()
        return self._file_download_probe_future

    def clear_file_download_probe(self) -> None:
        """Drop the temporary observer."""
        self._file_download_probe_future = None

    def parse_bc_data(self) -> None:
        """Teach the pinned reolink-aio parser that 0x6482 is a 24-byte class."""
        data = self._data
        if len(data) >= 20:
            raw_class = data[18:20]
            if raw_class == FILE_DOWNLOAD_CLASS_WIRE:
                self._patched_file_download_class = True
                patched = bytearray(data)
                patched[18:20] = MODERN_24_CLASS_WIRE
                self._data = bytes(patched)
                data = self._data

            cmd_id = int.from_bytes(data[4:8], "little")
            body_length = int.from_bytes(data[8:12], "little")
            if cmd_id == 13 and len(data) >= 24 + body_length:
                future = self._file_download_probe_future
                if future is not None and not future.done():
                    response_code = int.from_bytes(data[16:18], "little")
                    message_class = (
                        FILE_DOWNLOAD_MESSAGE_CLASS
                        if self._patched_file_download_class
                        else int.from_bytes(data[18:20], "little")
                    )
                    payload_offset = int.from_bytes(data[20:24], "little")
                    if payload_offset < 0 or payload_offset > body_length:
                        payload_offset = body_length
                    future.set_result(
                        FileDownloadFrameMetadata(
                            response_code=response_code,
                            message_class=message_class,
                            channel_id=data[12],
                            stream_type=data[13],
                            msg_num=int.from_bytes(data[14:16], "little"),
                            body_length=body_length,
                            payload_offset=payload_offset,
                            payload_length=max(0, body_length - payload_offset),
                        )
                    )
                self._patched_file_download_class = False

        super().parse_bc_data()

    def connection_lost(self, exc: Exception | None = None) -> None:
        future = self._file_download_probe_future
        if future is not None and not future.done():
            future.set_exception(exc or ConnectionError("Baichuan connection closed"))
        if not self.close_future.done():
            super().connection_lost(exc)


class BoundBaichuanUdpConnection(BaichuanUdpConnection):
    """Baichuan UDP connection pinned to the selected source IPv4."""

    def __init__(self, host: str, source_ip: str, *args, **kwargs) -> None:
        super().__init__(host, *args, **kwargs)
        self.source_ip = source_ip

    async def _create_connection(
        self,
    ) -> tuple[asyncio.DatagramTransport, BaichuanUdpClientProtocol]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.source_ip, 0))
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
        return transport, protocol

    async def send_file_download_probe(
        self, wire: bytes, *, timeout: float = 10.0
    ) -> FileDownloadFrameMetadata:
        """Send one prebuilt cmd13 frame and observe only its first response frame."""
        if not self.connection_open:
            await self.connect()
        protocol = self._protocol
        if not isinstance(protocol, _IdempotentUdpClientProtocol):
            raise RuntimeError("unexpected Baichuan UDP protocol")
        future = protocol.arm_file_download_probe()
        try:
            await self.send_without_wait(wire, cmd_id=13, timeout=min(timeout, 5.0))
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        finally:
            protocol.clear_file_download_probe()

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
