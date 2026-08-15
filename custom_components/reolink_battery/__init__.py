"""Reolink Battery integration."""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import CloudTokens, ReolinkCloudClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_EMAIL,
    CONF_ACCOUNT_PASSWORD,
    CONF_DEVICE_NAME,
    CONF_DEVICE_PASSWORD,
    CONF_DEVICE_USERNAME,
    CONF_INTERFACE,
    CONF_LOCAL_STATE,
    CONF_MFA_TRUST_TOKEN,
    CONF_MODEL,
    CONF_NOTIFICATION_ENTITY,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UID,
    CONF_USER_ID,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import ReolinkBatteryCoordinator
from .device_status import (
    CloudState,
    DeviceState,
    DeviceStatusCache,
    LocalState,
    local_state_as_dict,
    local_state_from_dict,
)

if TYPE_CHECKING:
    from .notification_bridge import NotificationBridge

PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON)
STORAGE_SENSOR_KEYS = ("storage_total", "storage_used", "storage_free")


class LocalStatusRefreshError(RuntimeError):
    """A secret-safe manual refresh failure."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


@dataclass(slots=True)
class ReolinkBatteryRuntime:
    """Runtime objects owned by one config entry."""

    cloud: ReolinkCloudClient
    coordinator: ReolinkBatteryCoordinator
    status: DeviceStatusCache
    local_operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    notification_bridge: NotificationBridge | None = None


ReolinkBatteryConfigEntry = ConfigEntry[ReolinkBatteryRuntime]


async def async_setup_entry(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> bool:
    """Set up cloud polling, queue and optional HA Companion notification bridge."""
    cloud = ReolinkCloudClient(async_get_clientsession(hass))

    def update_tokens(tokens: CloudTokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: tokens.access_token,
                CONF_REFRESH_TOKEN: tokens.refresh_token,
                CONF_MFA_TRUST_TOKEN: tokens.mfa_trust_token,
                CONF_USER_ID: tokens.user_id,
                CONF_TOKEN_EXPIRES_AT: tokens.expires_at,
            },
        )

    coordinator = ReolinkBatteryCoordinator(
        hass,
        entry.entry_id,
        cloud,
        CloudTokens(
            access_token=entry.data.get(CONF_ACCESS_TOKEN, ""),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN, ""),
            mfa_trust_token=entry.data.get(CONF_MFA_TRUST_TOKEN, ""),
            user_id=entry.data.get(CONF_USER_ID, ""),
            expires_at=float(entry.data.get(CONF_TOKEN_EXPIRES_AT, 0)),
        ),
        entry.data[CONF_ACCOUNT_EMAIL],
        entry.data[CONF_ACCOUNT_PASSWORD],
        entry.data[CONF_UID],
        update_tokens,
    )
    await coordinator.async_initialize()
    local_state = local_state_from_dict(entry.data.get(CONF_LOCAL_STATE))
    status = DeviceStatusCache(
        DeviceState(
            CloudState(model=entry.data.get(CONF_MODEL) or None),
            local_state,
        )
    )
    runtime = ReolinkBatteryRuntime(cloud, coordinator, status)
    entry.runtime_data = runtime

    notification_entity = entry.options.get(CONF_NOTIFICATION_ENTITY)
    if isinstance(notification_entity, str) and notification_entity:
        from .notification_bridge import NotificationBridge

        async def ingest_notification(event) -> int:
            return await coordinator.async_ingest_events([event])

        runtime.notification_bridge = NotificationBridge(
            hass,
            notification_entity,
            entry.data.get(CONF_DEVICE_NAME) or entry.title,
            entry.data[CONF_UID],
            ingest_notification,
        )
        runtime.notification_bridge.start()

    _migrate_storage_units(hass, entry)
    _update_device_registry(hass, entry, local_state)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(
        hass, coordinator.async_run(), "reolink_battery cloud event polling"
    )
    return True


def _migrate_storage_units(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> None:
    """Replace Alpha 3's automatically retained byte display with decimal GB."""
    registry = er.async_get(hass)
    for key in STORAGE_SENSOR_KEYS:
        entity_id = registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{entry.data[CONF_UID]}_{key}"
        )
        if entity_id is None or (registry_entry := registry.async_get(entity_id)) is None:
            continue
        private_options = dict(registry_entry.options.get("sensor.private", {}))
        if (
            private_options.get("suggested_unit_of_measurement")
            != UnitOfInformation.BYTES
        ):
            continue
        private_options.pop("suggested_unit_of_measurement")
        registry.async_update_entity(
            entity_id, unit_of_measurement=UnitOfInformation.GIGABYTES
        )
        registry.async_update_entity_options(
            entity_id, "sensor.private", private_options or None
        )


def _update_device_registry(
    hass: HomeAssistant,
    entry: ReolinkBatteryConfigEntry,
    local_state: LocalState | None,
) -> None:
    """Create/enrich DeviceInfo without replacing known values with nulls."""
    info = local_state.device_info if local_state is not None else None
    values = {
        "config_entry_id": entry.entry_id,
        "identifiers": {(DOMAIN, entry.data[CONF_UID])},
        "manufacturer": MANUFACTURER,
        "name": entry.data.get(CONF_DEVICE_NAME) or "Reolink battery camera",
    }
    model = info.model if info and info.model else entry.data.get(CONF_MODEL)
    if model:
        values["model"] = model
    if info and info.firmware:
        values["sw_version"] = info.firmware
    if info and info.hardware:
        values["hw_version"] = info.hardware
    dr.async_get(hass).async_get_or_create(**values)


def update_local_state(
    hass: HomeAssistant,
    entry: ReolinkBatteryConfigEntry,
    local_state: LocalState,
) -> None:
    """Cache/persist a snapshot produced by an already-open camera session."""
    entry.runtime_data.status.update_local(local_state)
    merged = entry.runtime_data.status.state.local
    assert merged is not None
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_LOCAL_STATE: local_state_as_dict(merged)},
    )
    _update_device_registry(hass, entry, merged)


async def async_refresh_local_status(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> LocalState:
    """Run one user-requested local status session and update its shared cache."""
    async with entry.runtime_data.local_operation_lock:
        # Keep protocol imports out of normal startup: loading the integration
        # must not initialize or contact the sleeping camera.
        from .camera import CameraStageError, async_validate_legacy_device

        previous = entry.runtime_data.status.state.local
        info = previous.device_info if previous is not None else None
        include_device_info = (
            info is None or not info.model or not info.firmware or not info.hardware
        )
        try:
            result = await async_validate_legacy_device(
                entry.data[CONF_UID],
                entry.data[CONF_DEVICE_USERNAME],
                entry.data[CONF_DEVICE_PASSWORD],
                ipaddress.ip_interface(entry.data[CONF_INTERFACE]),
                include_device_info=include_device_info,
            )
        except CameraStageError as err:
            raise LocalStatusRefreshError(err.stage) from None
        if result.local_state is None:
            raise LocalStatusRefreshError("LOCAL_STATUS_QUERY_ERROR")

        update_local_state(hass, entry, result.local_state)
        merged = entry.runtime_data.status.state.local
        assert merged is not None
        return merged


async def async_unload_entry(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> bool:
    """Persist the queue, stop listeners and stop polling."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    if entry.runtime_data.notification_bridge is not None:
        entry.runtime_data.notification_bridge.stop()
    await entry.runtime_data.coordinator.async_shutdown()
    return True
