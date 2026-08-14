"""Cached charging state; this platform never contacts the camera."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_DEVICE_NAME, CONF_MODEL, CONF_UID, DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ReolinkBatteryConfigEntry


DESCRIPTION = BinarySensorEntityDescription(
    key="charging",
    translation_key="charging",
    device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReolinkBatteryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities((ReolinkChargingBinarySensor(entry),))


class ReolinkChargingBinarySensor(BinarySensorEntity):
    """Presentation-only view over cached cmd253 state."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description = DESCRIPTION

    def __init__(self, entry: ReolinkBatteryConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_UID]}_charging"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._entry.runtime_data.status.add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool | None:
        local = self._entry.runtime_data.status.state.local
        return (
            None
            if local is None or local.battery is None
            else local.battery.charging
        )

    @property
    def available(self) -> bool:
        return self.is_on is not None

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
