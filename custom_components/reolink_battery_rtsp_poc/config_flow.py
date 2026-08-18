"""Config flow for the isolated Reolink Battery RTSP PoC."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_SOURCE_ENTRY_ID, DOMAIN, SOURCE_DOMAIN


class ReolinkBatteryRtspPocConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Select an existing Reolink Battery config entry as the PoC source."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        source_entries = self.hass.config_entries.async_entries(SOURCE_DOMAIN)
        if not source_entries:
            return self.async_abort(reason="no_source_entries")

        choices = {entry.entry_id: entry.title for entry in source_entries}
        if user_input is not None:
            source_entry_id = user_input[CONF_SOURCE_ENTRY_ID]
            source = next(
                entry
                for entry in source_entries
                if entry.entry_id == source_entry_id
            )
            await self.async_set_unique_id(source_entry_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"RTSP PoC - {source.title}",
                data={CONF_SOURCE_ENTRY_ID: source_entry_id},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_SOURCE_ENTRY_ID): vol.In(choices)}
            ),
        )
