"""Isolated Reolink Battery RTSP/Live View proof-of-concept integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_SOURCE_ENTRY_ID, DOMAIN, SOURCE_DOMAIN

PLATFORMS = (Platform.BUTTON,)


@dataclass(slots=True)
class ReolinkBatteryRtspPocRuntime:
    """Runtime data containing only a reference to the source config entry."""

    source_entry_id: str


ReolinkBatteryRtspPocConfigEntry = ConfigEntry[ReolinkBatteryRtspPocRuntime]


def source_entry_for(
    hass: HomeAssistant, source_entry_id: str
) -> ConfigEntry | None:
    """Resolve the referenced production Reolink Battery config entry."""
    return next(
        (
            item
            for item in hass.config_entries.async_entries(SOURCE_DOMAIN)
            if item.entry_id == source_entry_id
        ),
        None,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ReolinkBatteryRtspPocConfigEntry
) -> bool:
    """Set up the isolated PoC without modifying the source integration."""
    source_entry_id = entry.data[CONF_SOURCE_ENTRY_ID]
    if source_entry_for(hass, source_entry_id) is None:
        return False

    entry.runtime_data = ReolinkBatteryRtspPocRuntime(
        source_entry_id=source_entry_id
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ReolinkBatteryRtspPocConfigEntry
) -> bool:
    """Unload the isolated PoC."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
