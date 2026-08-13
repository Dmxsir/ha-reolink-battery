"""Reolink Battery integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import CloudTokens, ReolinkCloudClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_EMAIL,
    CONF_ACCOUNT_PASSWORD,
    CONF_DEVICE_NAME,
    CONF_MFA_TRUST_TOKEN,
    CONF_MODEL,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UID,
    CONF_USER_ID,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import ReolinkBatteryCoordinator


@dataclass(slots=True)
class ReolinkBatteryRuntime:
    """Runtime objects owned by one config entry."""

    cloud: ReolinkCloudClient
    coordinator: ReolinkBatteryCoordinator


ReolinkBatteryConfigEntry = ConfigEntry[ReolinkBatteryRuntime]


async def async_setup_entry(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> bool:
    """Set up cloud polling and the pending event queue."""
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
    entry.runtime_data = ReolinkBatteryRuntime(cloud, coordinator)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.data[CONF_UID])},
        manufacturer=MANUFACTURER,
        name=entry.data.get(CONF_DEVICE_NAME) or "Reolink battery camera",
        model=entry.data.get(CONF_MODEL) or None,
    )
    entry.async_create_background_task(
        hass, coordinator.async_run(), "reolink_battery cloud event polling"
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> bool:
    """Persist the queue and stop polling."""
    await entry.runtime_data.coordinator.async_shutdown()
    return True
