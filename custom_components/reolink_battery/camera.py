"""Battery-safe camera open/authenticate/close validation."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from dataclasses import dataclass

from reolink_aio.api import Host
from reolink_aio.enums import ConnectionEnum

from .transport import (
    BoundBaichuanUdpConnection,
    linux_ipv4_interface,
    resolve_uid_lan,
    validate_local_lan_route,
)


@dataclass(frozen=True, slots=True)
class CameraValidationResult:
    """Safe result from a short validation session."""

    transport_type: str
    resolve_seconds: float
    connect_seconds: float
    auth_seconds: float


async def async_validate_legacy_device(
    uid: str,
    username: str,
    password: str,
    interface: ipaddress.IPv4Interface,
    *,
    resolve_timeout: float = 10.0,
    command_timeout: int = 30,
) -> CameraValidationResult:
    """Wake, authenticate, and immediately close without media operations."""
    lease = None
    host = None
    connection = None
    resolve_started = time.monotonic()
    try:
        interface_name, _ = await asyncio.to_thread(
            linux_ipv4_interface, str(interface.ip)
        )
        lease = await asyncio.to_thread(
            resolve_uid_lan, uid, interface, resolve_timeout
        )
        resolve_seconds = time.monotonic() - resolve_started
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
        connection = BoundBaichuanUdpConnection(
            lease.host,
            lease.source_ip,
            0,
            host.baichuan._push_callback,
            host.baichuan._close_callback,
            uid=uid,
        )
        host.baichuan._connection = connection
        connect_started = time.monotonic()
        await connection.connect()
        connect_seconds = time.monotonic() - connect_started
        lease.close()
        lease = None
        auth_started = time.monotonic()
        # Argus 2E omits analogChnNum; Phase 2 proved login with this metadata
        # parsing path disabled. No HTTP discovery is required.
        host.baichuan._first_login = False
        await host.baichuan.login()
        auth_seconds = time.monotonic() - auth_started
        return CameraValidationResult(
            "LAN", resolve_seconds, connect_seconds, auth_seconds
        )
    finally:
        try:
            if host is not None:
                await host.logout()
            if connection is not None and connection.connection_open:
                await connection.close()
            if lease is not None:
                lease.close()
        finally:
            password = ""
