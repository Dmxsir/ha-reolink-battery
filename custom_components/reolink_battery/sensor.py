"""Cached local-status sensors; this platform never contacts the camera."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
)
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_DEVICE_NAME, CONF_MODEL, CONF_UID, DOMAIN, MANUFACTURER
from .device_status import LocalState

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ReolinkBatteryConfigEntry


DECIMAL_GIGABYTE = 1_000_000_000


def _storage_value(name: str) -> Callable[[LocalState], float | None]:
    return lambda state: (
        getattr(state.storage, name) / DECIMAL_GIGABYTE
        if state.storage is not None and getattr(state.storage, name) is not None
        else None
    )


@dataclass(frozen=True, kw_only=True)
class ReolinkStatusSensorDescription(SensorEntityDescription):
    value: Callable[[LocalState], float | int | datetime | None]


SENSORS = (
    ReolinkStatusSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value=lambda state: (
            state.battery.percent if state.battery is not None else None
        ),
    ),
    ReolinkStatusSensorDescription(
        key="storage_total",
        translation_key="storage_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=_storage_value("total_bytes"),
    ),
    ReolinkStatusSensorDescription(
        key="storage_used",
        translation_key="storage_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=_storage_value("used_bytes"),
    ),
    ReolinkStatusSensorDescription(
        key="storage_free",
        translation_key="storage_free",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=_storage_value("free_bytes"),
    ),
    ReolinkStatusSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda state: state.wifi_rssi_dbm,
    ),
    ReolinkStatusSensorDescription(
        key="last_local_refresh",
        translation_key="last_local_refresh",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda state: state.refreshed_at,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReolinkBatteryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(ReolinkStatusSensor(entry, description) for description in SENSORS)


class ReolinkStatusSensor(SensorEntity):
    """Presentation-only view over the local-state cache."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ReolinkBatteryConfigEntry,
        description: ReolinkStatusSensorDescription,
    ) -> None:
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_UID]}_{description.key}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._entry.runtime_data.status.add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> float | int | datetime | None:
        local = self._entry.runtime_data.status.state.local
        return None if local is None else self.entity_description.value(local)

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def device_info(self) -> DeviceInfo:
        state = self._entry.runtime_data.status.state
        info = state.local.device_info if state.local is not None else None
        values = {
            "identifiers": {(DOMAIN, self._entry.data[CONF_UID])},
            "manufacturer": MANUFACTURER,
            "name": self._entry.data.get(CONF_DEVICE_NAME)
            or "Reolink battery camera",
        }
        model = (
            (info.model if info and info.model else state.cloud.model)
            or self._entry.data.get(CONF_MODEL)
        )
        if model:
            values["model"] = model
        if info and info.firmware:
            values["sw_version"] = info.firmware
        if info and info.hardware:
            values["hw_version"] = info.hardware
        return DeviceInfo(**values)
