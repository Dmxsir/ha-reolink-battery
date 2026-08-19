"""Battery-safe camera validation and on-demand Live View entity."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.entity import DeviceInfo
from reolink_aio.api import Host
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_PASSWORD,
    CONF_DEVICE_USERNAME,
    CONF_INTERFACE,
    CONF_MODEL,
    CONF_UID,
    DOMAIN,
    MANUFACTURER,
)
from .device_status import (
    BatteryState,
    LocalState,
    parse_battery_info,
    parse_local_state,
)
from .transport import (
    BoundBaichuanUdpConnection,
    linux_ipv4_interface,
    resolve_uid_lan,
    validate_local_lan_route,
)

_LOGGER = logging.getLogger(__name__)


class CameraStageError(RuntimeError):
    """A secret-safe local camera failure stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class CameraValidationResult:
    """Safe result from a short validation session."""

    transport_type: str
    resolve_seconds: float
    connect_seconds: float
    auth_seconds: float
    local_state: LocalState | None


def prepare_standalone_channel_zero(host: Host) -> None:
    """Register the known standalone channel omitted by this Argus login."""
    if 0 not in host._channels:
        host._channels.append(0)
    if 0 not in host._stream_channels:
        host._stream_channels.append(0)
    host._num_channels = max(host._num_channels, 1)


async def async_get_battery_state(host: Host) -> BatteryState | None:
    """Read cmd253 through reolink_aio after its narrow channel-0 shim."""
    prepare_standalone_channel_zero(host)
    await host.baichuan.get_battery_info(0)
    return parse_battery_info(host._battery.get(0))


async def async_collect_local_state(
    host: Host, *, include_device_info: bool = True
) -> LocalState | None:
    """Collect optional status while an authenticated session is already open."""
    info = None
    battery = None
    storage = None
    wifi = None
    refreshed = False
    prepare_standalone_channel_zero(host)
    try:
        await host.baichuan.get_battery_info(0)
        battery = host._battery.get(0)
        refreshed = True
    except ReolinkError:
        _LOGGER.warning("BATTERY_QUERY_ERROR")
    try:
        await host.baichuan.GetHddInfo()
        storage = host.hdd_info
        refreshed = True
    except ReolinkError:
        _LOGGER.warning("STORAGE_QUERY_ERROR")
    try:
        await host.baichuan.get_wifi_signal()
        wifi = host.wifi_signal()
        refreshed = True
    except ReolinkError:
        _LOGGER.warning("WIFI_QUERY_ERROR")
    if include_device_info:
        try:
            info = await host.baichuan.get_info()
            refreshed = True
        except ReolinkError:
            _LOGGER.warning("DEVICE_INFO_QUERY_ERROR")
    if not refreshed:
        return None
    return parse_local_state(
        refreshed_at=datetime.now(UTC),
        device_info=info,
        battery=battery,
        storage=storage,
        wifi_signal=wifi,
    )


async def async_validate_legacy_device(
    uid: str,
    username: str,
    password: str,
    interface: ipaddress.IPv4Interface,
    *,
    resolve_timeout: float = 10.0,
    command_timeout: int = 30,
    include_device_info: bool = True,
) -> CameraValidationResult:
    """Wake, authenticate, and immediately close without media operations."""
    lease = None
    host = None
    connection = None
    failure_stage = "UID_RESOLVE_ERROR"
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
        failure_stage = "WAKE_ERROR"
        connect_started = time.monotonic()
        await connection.connect()
        connect_seconds = time.monotonic() - connect_started
        lease.close()
        lease = None
        auth_started = time.monotonic()
        failure_stage = "AUTH_ERROR"
        host.baichuan._first_login = False
        await host.baichuan.login()
        auth_seconds = time.monotonic() - auth_started
        local_state = await async_collect_local_state(
            host, include_device_info=include_device_info
        )
        return CameraValidationResult(
            "LAN", resolve_seconds, connect_seconds, auth_seconds, local_state
        )
    except (ReolinkError, OSError, TimeoutError):
        _LOGGER.warning("%s", failure_stage)
        raise CameraStageError(failure_stage) from None
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


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up the native Home Assistant Live View camera entity."""
    async_add_entities((ReolinkBatteryLiveCamera(entry),))


class ReolinkBatteryLiveCamera(Camera):
    """Expose battery-safe go2rtc Live View without background camera polling."""

    _attr_has_entity_name = True
    _attr_translation_key = "live_view"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry) -> None:
        super().__init__()
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_UID]}_live_view"

    @property
    def available(self) -> bool:
        runtime = self._entry.runtime_data
        bridge = getattr(runtime, "go2rtc_bridge", None)
        return bool(
            runtime is not None
            and bridge is not None
            and bridge.rtsp_url
            and self._entry.data.get(CONF_DEVICE_USERNAME)
            and self._entry.data.get(CONF_DEVICE_PASSWORD)
            and self._entry.data.get(CONF_INTERFACE)
        )

    @property
    def is_streaming(self) -> bool:
        runtime = self._entry.runtime_data
        hub = getattr(runtime, "live_hub", None)
        return bool(hub is not None and hub.is_active)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data[CONF_UID])},
            manufacturer=MANUFACTURER,
            model=self._entry.data.get(CONF_MODEL) or "Battery camera",
            name=self._entry.data.get(CONF_DEVICE_NAME) or "Reolink battery camera",
        )

    async def stream_source(self) -> str | None:
        """Return the on-demand go2rtc RTSP source."""
        runtime = self._entry.runtime_data
        bridge = getattr(runtime, "go2rtc_bridge", None)
        return bridge.rtsp_url if bridge is not None else None

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Never wake the battery camera merely to refresh a dashboard still."""
        return None
