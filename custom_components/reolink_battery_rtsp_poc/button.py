"""Manual controls for the isolated Reolink Battery RTSP PoC."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo

from . import ReolinkBatteryRtspPocConfigEntry, source_entry_for
from .const import (
    DOMAIN,
    MANUFACTURER,
    SOURCE_CONF_DEVICE_NAME,
    SOURCE_CONF_DEVICE_PASSWORD,
    SOURCE_CONF_DEVICE_USERNAME,
    SOURCE_CONF_INTERFACE,
    SOURCE_CONF_MODEL,
    SOURCE_CONF_UID,
)
from .live_stream_diagnostics import (
    apply_live_probe_error,
    apply_live_probe_result,
    reset_live_probe_state,
)
from .live_stream_probe import LiveStreamProbeError, async_probe_live_stream

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


PROBE_DESCRIPTION = ButtonEntityDescription(
    key="probe_live_stream",
    translation_key="probe_live_stream",
)


async def async_setup_entry(
    hass,
    entry: ReolinkBatteryRtspPocConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities((ReolinkProbeLiveStreamButton(entry),))


class ReolinkProbeLiveStreamButton(ButtonEntity):
    """Run one explicit 10-second main-stream probe."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description = PROBE_DESCRIPTION

    def __init__(self, entry: ReolinkBatteryRtspPocConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_probe_live_stream"

    def _source_entry(self):
        return source_entry_for(
            self.hass, self._entry.runtime_data.source_entry_id
        )

    @property
    def available(self) -> bool:
        source = self._source_entry()
        return bool(
            source is not None
            and source.runtime_data is not None
            and getattr(source.runtime_data, "local_operation_lock", None) is not None
        )

    @property
    def device_info(self) -> DeviceInfo:
        source = self._source_entry()
        source_data = source.data if source is not None else {}
        uid = source_data.get(SOURCE_CONF_UID, self._entry.entry_id)
        return DeviceInfo(
            identifiers={(DOMAIN, uid)},
            manufacturer=MANUFACTURER,
            model=source_data.get(SOURCE_CONF_MODEL, "Battery camera"),
            name=f"RTSP PoC - {source_data.get(SOURCE_CONF_DEVICE_NAME, 'Reolink Battery')}",
        )

    async def async_press(self) -> None:
        source = self._source_entry()
        if source is None:
            raise HomeAssistantError("SOURCE_INTEGRATION_NOT_FOUND")
        if source.runtime_data is None:
            raise HomeAssistantError("SOURCE_INTEGRATION_NOT_LOADED")

        lock = getattr(source.runtime_data, "local_operation_lock", None)
        if lock is None:
            raise HomeAssistantError("SOURCE_OPERATION_LOCK_UNAVAILABLE")

        required = (
            SOURCE_CONF_UID,
            SOURCE_CONF_DEVICE_USERNAME,
            SOURCE_CONF_DEVICE_PASSWORD,
            SOURCE_CONF_INTERFACE,
        )
        if any(not source.data.get(key) for key in required):
            raise HomeAssistantError("SOURCE_LOCAL_CREDENTIALS_INCOMPLETE")

        reset_live_probe_state(self._entry.entry_id, stream_kind="main")
        try:
            # Share only the source integration's operation lock. The PoC has its
            # own transport/session, so a recording download and live probe can
            # never wake/use the battery camera concurrently.
            async with lock:
                result = await async_probe_live_stream(
                    source.data[SOURCE_CONF_UID],
                    source.data[SOURCE_CONF_DEVICE_USERNAME],
                    source.data[SOURCE_CONF_DEVICE_PASSWORD],
                    ipaddress.ip_interface(source.data[SOURCE_CONF_INTERFACE]),
                    stream="main",
                    duration=10.0,
                )
        except LiveStreamProbeError as err:
            apply_live_probe_error(self._entry.entry_id, err)
            raise HomeAssistantError(err.stage) from None

        apply_live_probe_result(self._entry.entry_id, result)
        if not result.trace.bcmedia_observed:
            raise HomeAssistantError("LIVE_MEDIA_NOT_OBSERVED")
