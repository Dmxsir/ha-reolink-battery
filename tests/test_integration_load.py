"""Synthetic Home Assistant setup/unload test; no network is contacted."""

from __future__ import annotations

import asyncio
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
    BUTTON = "button"
    BATTERY = "battery"
    BATTERY_CHARGING = "battery_charging"
    DATA_SIZE = "data_size"
    SIGNAL_STRENGTH = "signal_strength"
    TIMESTAMP = "timestamp"
    MEASUREMENT = "measurement"
    DIAGNOSTIC = "diagnostic"
    BYTES = "B"
    GIGABYTES = "GB"


@dataclass(frozen=True, kw_only=True)
class _Description:
    key: str
    translation_key: str | None = None
    device_class: object | None = None
    state_class: object | None = None
    native_unit_of_measurement: object | None = None
    entity_category: object | None = None
    suggested_display_precision: int | None = None


class _Entity:
    async def async_added_to_hass(self):
        return None

    def async_on_remove(self, remove):
        self._remove = remove

    def async_write_ha_state(self):
        self.write_count = getattr(self, "write_count", 0) + 1


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


class _EntityRegistry:
    def __init__(self):
        self.entries = {}

    def async_get_entity_id(self, _domain, _platform, unique_id):
        return next(
            (
                entity_id
                for entity_id, entry in self.entries.items()
                if entry.unique_id == unique_id
            ),
            None,
        )

    def async_get(self, entity_id):
        return self.entries.get(entity_id)

    def async_update_entity(self, entity_id, *, unit_of_measurement):
        self.entries[entity_id].unit_of_measurement = unit_of_measurement

    def async_update_entity_options(self, entity_id, domain, options):
        entry = self.entries[entity_id]
        entry.options.pop(domain, None)
        if options is not None:
            entry.options[domain] = options


ENTITY_REGISTRY = _EntityRegistry()


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
    button = types.ModuleType("homeassistant.components.button")
    button.ButtonEntityDescription = _Description
    button.ButtonEntity = _Entity
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
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda _hass: ENTITY_REGISTRY
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity.DeviceInfo = _DeviceInfo
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _Store
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.sensor": sensor,
            "homeassistant.components.binary_sensor": binary_sensor,
            "homeassistant.components.button": button,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.aiohttp_client": aiohttp_client,
            "homeassistant.helpers.device_registry": device_registry,
            "homeassistant.helpers.entity_registry": entity_registry,
            "homeassistant.helpers.entity": entity,
            "homeassistant.helpers.storage": storage,
            "homeassistant.exceptions": exceptions,
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
            first_new_entity = len(self.entities)
            await module.async_setup_entry(
                entry.hass,
                entry,
                lambda entities: self.entities.extend(list(entities)),
            )
            for entity in self.entities[first_new_entity:]:
                entity.hass = entry.hass

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
    def _local_state(self, *, percent=100, wifi=-30):
        return status.LocalState(
            refreshed_at=datetime(2026, 8, 14, 8, 30, tzinfo=UTC),
            device_info=status.DeviceInfo(
                model="Reolink Argus 2E",
                firmware="0616_722_52_478",
                hardware="BIPC_36S7616MC94",
            ),
            battery=status.BatteryState(
                percent=percent,
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
            wifi_rssi_dbm=wifi,
        )

    def _entry(self, hass, local=None):
        data = {
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
            const.CONF_DEVICE_USERNAME: "admin",
            const.CONF_DEVICE_PASSWORD: "synthetic-password",
            const.CONF_INTERFACE: "192.0.2.61/24",
        }
        if local is not None:
            data[const.CONF_LOCAL_STATE] = status.local_state_as_dict(local)
        return _Entry(hass, data)

    @staticmethod
    def _button(hass):
        return next(
            entity
            for entity in hass.config_entries.entities
            if entity._attr_unique_id.endswith("_refresh_device_status")
        )

    @staticmethod
    def _install_camera_fake(refresh):
        class CameraStageError(RuntimeError):
            def __init__(self, stage):
                super().__init__(stage)
                self.stage = stage

        module = types.ModuleType(f"{PACKAGE}.camera")
        module.CameraStageError = CameraStageError
        module.async_validate_legacy_device = refresh
        sys.modules[f"{PACKAGE}.camera"] = module
        return CameraStageError

    async def asyncTearDown(self):
        sys.modules.pop(f"{PACKAGE}.camera", None)

    async def test_setup_and_unload_restore_cache_without_camera_polling(self):
        hass = _Hass()
        local = self._local_state()
        entry = self._entry(hass, local)
        sys.modules.pop(f"{PACKAGE}.camera", None)
        ENTITY_REGISTRY.entries = {
            f"sensor.front_camera_{key}": types.SimpleNamespace(
                unique_id=f"camera-1_{key}",
                unit_of_measurement="B",
                options={
                    "sensor.private": {
                        "suggested_unit_of_measurement": "B",
                        "keep": True,
                    }
                },
            )
            for key in integration.STORAGE_SENSOR_KEYS
        }

        self.assertTrue(await integration.async_setup_entry(hass, entry))
        self.assertEqual(
            hass.config_entries.forwarded,
            (_Value.SENSOR, _Value.BINARY_SENSOR, _Value.BUTTON),
        )
        self.assertEqual(len(hass.config_entries.entities), 8)
        self.assertEqual(entry.runtime_data.status.state.local, local)
        self.assertTrue(
            all(
                registry_entry.unit_of_measurement == "GB"
                and registry_entry.options["sensor.private"] == {"keep": True}
                for registry_entry in ENTITY_REGISTRY.entries.values()
            )
        )
        self.assertNotIn(f"{PACKAGE}.camera", sys.modules)
        self.assertTrue(
            all(
                entity._attr_should_poll is False
                for entity in hass.config_entries.entities
            )
        )
        button = self._button(hass)
        self.assertEqual(
            button.device_info["identifiers"],
            {(const.DOMAIN, "camera-1")},
        )
        self.assertEqual(REGISTRY.created[-1]["model"], "Reolink Argus 2E")
        self.assertEqual(REGISTRY.created[-1]["sw_version"], "0616_722_52_478")
        self.assertEqual(REGISTRY.created[-1]["hw_version"], "BIPC_36S7616MC94")
        self.assertEqual(len(entry.background_tasks), 1)

        self.assertTrue(await integration.async_unload_entry(hass, entry))
        self.assertEqual(
            hass.config_entries.unloaded,
            (_Value.SENSOR, _Value.BINARY_SENSOR, _Value.BUTTON),
        )

    async def test_successful_press_updates_cache_and_entities_immediately(self):
        hass = _Hass()
        entry = self._entry(hass, self._local_state())
        await integration.async_setup_entry(hass, entry)
        state_entities = [
            entity
            for entity in hass.config_entries.entities
            if not entity._attr_unique_id.endswith("_refresh_device_status")
        ]
        for entity in state_entities:
            await entity.async_added_to_hass()

        refreshed = self._local_state(percent=87, wifi=-42)
        calls = []

        async def refresh(*args, **kwargs):
            calls.append((args, kwargs))
            return types.SimpleNamespace(local_state=refreshed)

        self._install_camera_fake(refresh)
        await self._button(hass).async_press()

        local = entry.runtime_data.status.state.local
        self.assertEqual(local.battery.percent, 87)
        self.assertEqual(local.wifi_rssi_dbm, -42)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]["include_device_info"])
        self.assertTrue(all(entity.write_count == 1 for entity in state_entities))
        self.assertEqual(
            entry.data[const.CONF_LOCAL_STATE]["battery"]["percent"], 87
        )

    async def test_repeated_presses_never_open_concurrent_sessions(self):
        hass = _Hass()
        entry = self._entry(hass, self._local_state())
        await integration.async_setup_entry(hass, entry)
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = active = maximum = 0

        async def refresh(*_args, **_kwargs):
            nonlocal calls, active, maximum
            calls += 1
            active += 1
            maximum = max(maximum, active)
            entered.set()
            await release.wait()
            active -= 1
            return types.SimpleNamespace(local_state=self._local_state())

        self._install_camera_fake(refresh)
        button = self._button(hass)
        first = asyncio.create_task(button.async_press())
        await entered.wait()
        second = asyncio.create_task(button.async_press())
        await asyncio.sleep(0)
        self.assertEqual(calls, 1)
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(calls, 2)
        self.assertEqual(maximum, 1)

    async def test_auth_failure_preserves_previous_cache(self):
        hass = _Hass()
        previous = self._local_state()
        entry = self._entry(hass, previous)
        await integration.async_setup_entry(hass, entry)

        async def refresh(*_args, **_kwargs):
            raise camera_error("AUTH_ERROR")

        camera_error = self._install_camera_fake(refresh)
        home_assistant_error = sys.modules[
            "homeassistant.exceptions"
        ].HomeAssistantError
        with self.assertRaisesRegex(home_assistant_error, "AUTH_ERROR"):
            await self._button(hass).async_press()
        self.assertEqual(entry.runtime_data.status.state.local, previous)
        self.assertEqual(
            status.local_state_from_dict(entry.data[const.CONF_LOCAL_STATE]),
            previous,
        )


if __name__ == "__main__":
    unittest.main()
