"""Manual local-status refresh button."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo

from . import (
    LocalStatusRefreshError,
    ReolinkBatteryConfigEntry,
    async_refresh_local_status,
)
from .const import CONF_UID, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


DESCRIPTION = ButtonEntityDescription(
    key="refresh_device_status",
    translation_key="refresh_device_status",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReolinkBatteryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the manual refresh button."""
    async_add_entities((ReolinkRefreshDeviceStatusButton(entry),))


class ReolinkRefreshDeviceStatusButton(ButtonEntity):
    """Start one short status-only camera session on explicit press."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description = DESCRIPTION

    def __init__(self, entry: ReolinkBatteryConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_UID]}_refresh_device_status"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data[CONF_UID])})

    async def async_press(self) -> None:
        """Refresh cached local status once."""
        try:
            await async_refresh_local_status(self.hass, self._entry)
        except LocalStatusRefreshError as err:
            raise HomeAssistantError(err.stage) from None
