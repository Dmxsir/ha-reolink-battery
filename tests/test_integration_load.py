"""Synthetic Home Assistant setup/unload test; no network is contacted."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_load_test"


class _Value(StrEnum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    BATTERY = "battery"
    BATTERY_CHARGING = "battery_charging"
    DATA_SIZE = "data_size"
    SIGNAL_STRENGTH = "signal_strength"
    TIMESTAMP = "timestamp"
    MEASUREMENT = "measurement"
    DIAGNOSTIC = "diagnostic"
    BYTES = "B"


@dataclass(frozen=True, kw_only=True)
class _Description:
    key: str
    translation_key: str | None = None
    device_class: object | None = None
    state_class: object | None = None
    native_unit_of_measurement: object | None = None
    entity_category: object | None = None


class _Entity:
    async def async_added_to_hass(self):
        return None

    def async_on_remove(self, remove):
        self._remove = remove

    def async_write_ha_state(self):
        return None


class _DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(kwargs)


class _ConfigEntry:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls


class _Store:
    def __init__(self, *_args, **_kwargs):
        self.saved = []

    async def async_load(self):
        return None

    async def async_save(self, value):
        self.saved.append(value)


class _Registry:
    def __init__(self):
        self.created = []

    def async_get_or_create(self, **values):
        self.created.append(values)
        return values


REGISTRY = _Registry()


def install_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor = types.ModuleType("homeassistant.components.sensor")
    sensor.SensorDeviceClass = _Value
    sensor.SensorStateClass = _Value
    sensor.SensorEntityDescription = _Description
    sensor.SensorEntity = _Entity
    binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorDeviceClass = _Value
    binary_sensor.BinarySensorEntityDescription = _Description
    binary_sensor.BinarySensorEntity = _Entity
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = _ConfigEntry
    const = types.ModuleType("homeassistant.const")
    const.Platform = _Value
    const.PERCENTAGE = "%"
    const.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"
    const.EntityCategory = _Value
    const.UnitOfInformation = _Value
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: object()
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda _hass: REGISTRY
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity.DeviceInfo = _DeviceInfo
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _Store
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.sensor": sensor,
            "homeassistant.components.binary_sensor": binary_sensor,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.aiohttp_client": aiohttp_client,
            "homeassistant.helpers.device_registry": device_registry,
            "homeassistant.helpers.entity": entity,
            "homeassistant.helpers.storage": storage,
        }
    )


install_stubs()
spec = importlib.util.spec_from_file_location(
    PACKAGE,
    COMPONENT / "__init__.py",
    submodule_search_locations=[str(COMPONENT)],
)
assert spec is not None and spec.loader is not None
integration = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE] = integration
spec.loader.exec_module(integration)
const = importlib.import_module(f"{PACKAGE}.const")
status = importlib.import_module(f"{PACKAGE}.device_status")


class _ConfigEntries:
    def __init__(self):
        self.entities = []
        self.forwarded = None
        self.unloaded = None

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded = tuple(platforms)
        for platform in platforms:
            module = importlib.import_module(f"{PACKAGE}.{platform.value}")
            await module.async_setup_entry(
                entry.hass,
                entry,
                lambda entities: self.entities.extend(list(entities)),
            )

    async def async_unload_platforms(self, _entry, platforms):
        self.unloaded = tuple(platforms)
        return True

    def async_update_entry(self, entry, *, data):
        entry.data = data


class _Hass:
    def __init__(self):
        self.config_entries = _ConfigEntries()


class _Entry(_ConfigEntry):
    def __init__(self, hass, data):
        self.hass = hass
        self.entry_id = "entry-1"
        self.data = data
        self.runtime_data = None
        self.background_tasks = []

    def async_create_background_task(self, _hass, coroutine, name):
        coroutine.close()
        self.background_tasks.append(name)


class IntegrationLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_and_unload_restore_cache_without_camera_polling(self):
        hass = _Hass()
        local = status.LocalState(
            refreshed_at=datetime(2026, 8, 14, 8, 30, tzinfo=UTC),
            device_info=status.DeviceInfo(
                model="Reolink Argus 2E",
                firmware="0616_722_52_478",
                hardware="BIPC_36S7616MC94",
            ),
            battery=status.BatteryState(
                percent=100,
                charging=True,
                charge_status="charging",
                power_source="unknown",
                adapter_status="unknown",
                low_power=None,
                temperature=34,
            ),
            storage=status.StorageState(
                present=True,
                status="normal",
                total_bytes=14_857_000_000,
                used_bytes=14_311_000_000,
                free_bytes=546_000_000,
            ),
            wifi_rssi_dbm=-30,
        )
        entry = _Entry(
            hass,
            {
                const.CONF_ACCOUNT_EMAIL: "account@example.invalid",
                const.CONF_ACCOUNT_PASSWORD: "unused",
                const.CONF_ACCESS_TOKEN: "",
                const.CONF_REFRESH_TOKEN: "",
                const.CONF_MFA_TRUST_TOKEN: "",
                const.CONF_USER_ID: "user-1",
                const.CONF_TOKEN_EXPIRES_AT: 0,
                const.CONF_UID: "camera-1",
                const.CONF_DEVICE_NAME: "Front camera",
                const.CONF_MODEL: "Argus 2E",
                const.CONF_LOCAL_STATE: status.local_state_as_dict(local),
            },
        )

        self.assertTrue(await integration.async_setup_entry(hass, entry))
        self.assertEqual(
            hass.config_entries.forwarded,
            (_Value.SENSOR, _Value.BINARY_SENSOR),
        )
        self.assertEqual(len(hass.config_entries.entities), 7)
        self.assertEqual(entry.runtime_data.status.state.local, local)
        self.assertNotIn(f"{PACKAGE}.camera", sys.modules)
        self.assertEqual(REGISTRY.created[-1]["model"], "Reolink Argus 2E")
        self.assertEqual(REGISTRY.created[-1]["sw_version"], "0616_722_52_478")
        self.assertEqual(REGISTRY.created[-1]["hw_version"], "BIPC_36S7616MC94")
        self.assertEqual(len(entry.background_tasks), 1)

        self.assertTrue(await integration.async_unload_entry(hass, entry))
        self.assertEqual(
            hass.config_entries.unloaded,
            (_Value.SENSOR, _Value.BINARY_SENSOR),
        )


if __name__ == "__main__":
    unittest.main()
