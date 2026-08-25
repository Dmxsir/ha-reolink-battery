"""Cached local-status and recording-queue sensors; never wake the camera."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory, UnitOfInformation
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_DEVICE_NAME, CONF_MODEL, CONF_UID, DOMAIN, MANUFACTURER
from .device_status import LocalState
from .events import is_automatic_event_fresh

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from . import ReolinkBatteryConfigEntry

DECIMAL_GIGABYTE = 1_000_000_000


def _storage_value(name: str) -> Callable[[LocalState], float | None]:
    return lambda state: getattr(state.storage, name) / DECIMAL_GIGABYTE if state.storage is not None and getattr(state.storage, name) is not None else None


@dataclass(frozen=True, kw_only=True)
class ReolinkStatusSensorDescription(SensorEntityDescription):
    value: Callable[[LocalState], float | int | datetime | None]


SENSORS = (
    ReolinkStatusSensorDescription(key="battery", translation_key="battery", device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=PERCENTAGE, value=lambda state: state.battery.percent if state.battery is not None else None),
    ReolinkStatusSensorDescription(key="storage_total", translation_key="storage_total", device_class=SensorDeviceClass.DATA_SIZE, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=UnitOfInformation.GIGABYTES, suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC, value=_storage_value("total_bytes")),
    ReolinkStatusSensorDescription(key="storage_used", translation_key="storage_used", device_class=SensorDeviceClass.DATA_SIZE, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=UnitOfInformation.GIGABYTES, suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC, value=_storage_value("used_bytes")),
    ReolinkStatusSensorDescription(key="storage_free", translation_key="storage_free", device_class=SensorDeviceClass.DATA_SIZE, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=UnitOfInformation.GIGABYTES, suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC, value=_storage_value("free_bytes")),
    ReolinkStatusSensorDescription(key="wifi_signal", translation_key="wifi_signal", device_class=SensorDeviceClass.SIGNAL_STRENGTH, state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, entity_category=EntityCategory.DIAGNOSTIC, value=lambda state: state.wifi_rssi_dbm),
    ReolinkStatusSensorDescription(key="last_local_refresh", translation_key="last_local_refresh", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC, value=lambda state: state.refreshed_at),
)

QUEUE_DESCRIPTION = SensorEntityDescription(key="recordings_queued", translation_key="recordings_queued", state_class=SensorStateClass.MEASUREMENT)


async def async_setup_entry(hass: HomeAssistant, entry: ReolinkBatteryConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entities: list[SensorEntity] = [ReolinkStatusSensor(entry, description) for description in SENSORS]
    if entry.runtime_data.recording_worker is not None:
        entities.append(ReolinkRecordingQueueSensor(entry))
    async_add_entities(entities)


class ReolinkStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ReolinkBatteryConfigEntry, description: ReolinkStatusSensorDescription) -> None:
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_UID]}_{description.key}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._entry.runtime_data.status.add_listener(self.async_write_ha_state))

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
        values = {"identifiers": {(DOMAIN, self._entry.data[CONF_UID])}, "manufacturer": MANUFACTURER, "name": self._entry.data.get(CONF_DEVICE_NAME) or "Reolink battery camera"}
        model = ((info.model if info and info.model else state.cloud.model) or self._entry.data.get(CONF_MODEL))
        if model:
            values["model"] = model
        if info and info.firmware:
            values["sw_version"] = info.firmware
        if info and info.hardware:
            values["hw_version"] = info.hardware
        return DeviceInfo(**values)


class ReolinkRecordingQueueSensor(SensorEntity):
    """Count pending Android recording events without contacting the camera."""
    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_icon = "mdi:video-outline"
    entity_description = QUEUE_DESCRIPTION

    def __init__(self, entry: ReolinkBatteryConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_UID]}_recordings_queued"

    def _android_pending(self):
        return [event for event in self._entry.runtime_data.coordinator.pending_events if event.source == "android_notification"]

    @property
    def native_value(self) -> int:
        return len(self._android_pending())

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        now = datetime.now(UTC)
        coordinator = self._entry.runtime_data.coordinator
        pending = self._android_pending()
        return {
            "deferred": sum(coordinator.is_event_deferred(event.event_id) for event in pending),
            "fresh_automatic": sum(not coordinator.is_event_deferred(event.event_id) and is_automatic_event_fresh(event, now) for event in pending),
            "stale": sum(not is_automatic_event_fresh(event, now) for event in pending),
        }

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data[CONF_UID])})
