"""Offline Home Assistant presentation tests; no camera is contacted."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_sensor_test"


class _Value(StrEnum):
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


class _SensorEntity:
    async def async_added_to_hass(self):
        return None

    def async_on_remove(self, remove):
        self._remove = remove

    def async_write_ha_state(self):
        self.write_count = getattr(self, "write_count", 0) + 1


class _DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(kwargs)


def install_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor = types.ModuleType("homeassistant.components.sensor")
    sensor.SensorDeviceClass = _Value
    sensor.SensorStateClass = _Value
    sensor.SensorEntityDescription = _Description
    sensor.SensorEntity = _SensorEntity
    binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorDeviceClass = _Value
    binary_sensor.BinarySensorEntityDescription = _Description
    binary_sensor.BinarySensorEntity = _SensorEntity
    const = types.ModuleType("homeassistant.const")
    const.PERCENTAGE = "%"
    const.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"
    const.EntityCategory = _Value
    const.UnitOfInformation = _Value
    helpers = types.ModuleType("homeassistant.helpers")
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity.DeviceInfo = _DeviceInfo
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.sensor": sensor,
            "homeassistant.components.binary_sensor": binary_sensor,
            "homeassistant.const": const,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.entity": entity,
        }
    )


install_stubs()
package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package


def load_module(name: str):
    full_name = f"{PACKAGE}.{name}"
    spec = importlib.util.spec_from_file_location(full_name, COMPONENT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


const = load_module("const")
status = load_module("device_status")
sensors = load_module("sensor")
binary_sensors = load_module("binary_sensor")


class SensorTests(unittest.TestCase):
    def test_unavailable_then_cached_values_device_info_and_no_polling(self):
        cache = status.DeviceStatusCache(
            status.DeviceState(status.CloudState(model="Argus 2E"))
        )
        entry = types.SimpleNamespace(
            data={
                const.CONF_UID: "camera-1",
                const.CONF_DEVICE_NAME: "Front camera",
                const.CONF_MODEL: "Argus 2E",
            },
            runtime_data=types.SimpleNamespace(status=cache),
        )
        entities = [sensors.ReolinkStatusSensor(entry, item) for item in sensors.SENSORS]
        self.assertTrue(all(entity.native_value is None for entity in entities))
        self.assertTrue(all(not entity.available for entity in entities))
        self.assertTrue(all(entity._attr_should_poll is False for entity in entities))
        self.assertNotIn("async_update", sensors.ReolinkStatusSensor.__dict__)
        charging = binary_sensors.ReolinkChargingBinarySensor(entry)
        self.assertIsNone(charging.is_on)
        self.assertFalse(charging.available)
        self.assertFalse(charging._attr_should_poll)

        cache.state = status.DeviceState(status.CloudState())
        entry.data[const.CONF_MODEL] = ""
        self.assertNotIn("model", entities[0].device_info)
        self.assertNotIn("sw_version", entities[0].device_info)
        self.assertNotIn("hw_version", entities[0].device_info)
        cache.state = status.DeviceState(status.CloudState(model="Argus 2E"))
        entry.data[const.CONF_MODEL] = "Argus 2E"

        local = status.parse_local_state(
            refreshed_at=datetime(2026, 8, 14, 8, 30, tzinfo=UTC),
            device_info={
                "type": "Reolink Argus 2E",
                "firmwareVersion": "0616_722_52_478",
                "hardwareVersion": "BIPC_36S7616MC94",
            },
            battery={
                "batteryPercent": 100,
                "chargeStatus": "charging",
                "adapterStatus": "unknown",
                "temperature": 34,
            },
            storage=[
                {
                    "storageType": 2,
                    "format": 1,
                    "mount": 1,
                    "capacity": 14_857,
                    "size": 546,
                }
            ],
            wifi_signal=-30,
        )
        cache.update_local(local)
        values = {entity.entity_description.key: entity.native_value for entity in entities}
        self.assertEqual(values["battery"], 100)
        self.assertEqual(values["storage_total"], 14_857_000_000)
        self.assertEqual(values["storage_used"], 14_311_000_000)
        self.assertEqual(values["storage_free"], 546_000_000)
        self.assertEqual(values["wifi_signal"], -30)
        self.assertEqual(values["last_local_refresh"], local.refreshed_at)
        self.assertTrue(all(entity.available for entity in entities))
        self.assertTrue(charging.is_on)
        self.assertTrue(charging.available)
        info = entities[0].device_info
        self.assertEqual(info["model"], "Reolink Argus 2E")
        self.assertEqual(info["sw_version"], "0616_722_52_478")
        self.assertEqual(info["hw_version"], "BIPC_36S7616MC94")

        asyncio.run(entities[0].async_added_to_hass())
        cache.update_local(local)
        self.assertEqual(entities[0].write_count, 1)


if __name__ == "__main__":
    unittest.main()
