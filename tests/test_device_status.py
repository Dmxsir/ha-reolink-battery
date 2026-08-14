"""Deterministic device-status parser tests; no camera is contacted."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "reolink_battery"
MODULE_NAME = "_reolink_battery_device_status_test"
spec = importlib.util.spec_from_file_location(
    MODULE_NAME, COMPONENT / "device_status.py"
)
assert spec is not None and spec.loader is not None
status = importlib.util.module_from_spec(spec)
sys.modules[MODULE_NAME] = status
spec.loader.exec_module(status)


class BatteryTests(unittest.TestCase):
    def test_battery_percentage_charging_and_solar(self):
        parsed = status.parse_battery_info(
            {
                "batteryPercent": 82,
                "chargeStatus": 1,
                "adapterStatus": "solarPanels",
                "lowPower": 0,
                "temperature": 28,
            }
        )
        self.assertEqual(parsed.percent, 82)
        self.assertTrue(parsed.charging)
        self.assertEqual(parsed.charge_status, "charging")
        self.assertEqual(parsed.power_source, "solar")
        self.assertFalse(parsed.low_power)
        self.assertEqual(parsed.temperature, 28)

    def test_completed_external_and_missing_battery(self):
        parsed = status.parse_battery_info(
            {
                "batteryPercent": "100",
                "chargeStatus": "completed",
                "adapterStatus": "DCAdapter",
            }
        )
        self.assertFalse(parsed.charging)
        self.assertEqual(parsed.charge_status, "complete")
        self.assertEqual(parsed.power_source, "external")
        self.assertIsNone(status.parse_battery_info(None))
        self.assertIsNone(status.parse_battery_info({"chargeStatus": "charging"}))

    def test_absent_optional_battery_fields_stay_unknown(self):
        parsed = status.parse_battery_info({"batteryPercent": 73})
        self.assertEqual(parsed.percent, 73)
        self.assertIsNone(parsed.charging)
        self.assertEqual(parsed.charge_status, "unknown")
        self.assertEqual(parsed.power_source, "unknown")
        self.assertIsNone(parsed.low_power)
        self.assertIsNone(parsed.temperature)


class StorageTests(unittest.TestCase):
    def test_storage_present_and_decimal_size_conversion(self):
        parsed = status.parse_storage_info(
            [
                {
                    "storageType": 2,
                    "format": 1,
                    "mount": 1,
                    "capacity": 64_000,
                    "size": 10_000,
                }
            ]
        )
        self.assertTrue(parsed.present)
        self.assertEqual(parsed.status, "normal")
        self.assertEqual(parsed.total_bytes, 64_000_000_000)
        self.assertEqual(parsed.free_bytes, 10_000_000_000)
        self.assertEqual(parsed.used_bytes, 54_000_000_000)

    def test_storage_absent_and_error(self):
        absent = status.parse_storage_info([])
        self.assertFalse(absent.present)
        self.assertEqual(absent.status, "absent")

        error = status.parse_storage_info(
            [{"storageType": 2, "format": 1, "mount": 0, "capacity": 64, "size": 8}]
        )
        self.assertTrue(error.present)
        self.assertEqual(error.status, "error")

        invalid_size = status.parse_storage_info(
            [{"storageType": 2, "format": 1, "mount": 1, "capacity": 8, "size": 9}]
        )
        self.assertEqual(invalid_size.status, "error")
        self.assertIsNone(invalid_size.used_bytes)


class DeviceStateTests(unittest.TestCase):
    def test_wifi_rssi_and_missing_optional_device_info(self):
        self.assertEqual(status.parse_wifi_rssi("-61"), -61)
        self.assertIsNone(status.parse_wifi_rssi(4))
        self.assertEqual(status.parse_device_info(None), status.DeviceInfo())
        self.assertEqual(
            status.parse_device_info({"type": "Argus 2E"}).model, "Argus 2E"
        )

    def test_cached_local_state_survives_cloud_update(self):
        refreshed = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
        local = status.parse_local_state(
            refreshed_at=refreshed,
            device_info={"type": "Argus 2E"},
            battery={"batteryPercent": 82, "chargeStatus": 0, "adapterStatus": "none"},
            storage=[],
            wifi_signal=-62,
        )
        device = status.DeviceState(status.CloudState(model="Argus 2E"), local)
        updated = device.with_cloud(
            status.CloudState(
                model="Argus 2E",
                status="bound",
                last_event_at=datetime(2026, 8, 14, 8, 5, tzinfo=UTC),
                last_event_type="MOTION",
            )
        )
        self.assertIs(updated.local, local)
        self.assertEqual(updated.local.refreshed_at, refreshed)
        self.assertEqual(updated.cloud.last_event_type, "MOTION")

    def test_local_update_does_not_modify_cloud_state(self):
        cloud = status.CloudState(model="Argus 2E", status="bound")
        device = status.DeviceState(cloud)
        local = status.parse_local_state(
            refreshed_at=datetime(2026, 8, 14, tzinfo=UTC),
            device_info={},
            battery=None,
            storage=None,
            wifi_signal=None,
        )
        updated = device.with_local(local)
        self.assertIs(updated.cloud, cloud)
        self.assertIsNone(updated.local.battery)
        self.assertIsNone(updated.local.storage)

    def test_cache_merges_optional_values_notifies_and_round_trips(self):
        first = status.parse_local_state(
            refreshed_at=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
            device_info={
                "type": "Reolink Argus 2E",
                "firmwareVersion": "firmware-1",
                "hardwareVersion": "hardware-1",
            },
            battery=None,
            storage=[
                {
                    "storageType": 2,
                    "format": 1,
                    "mount": 1,
                    "capacity": 100,
                    "size": 25,
                }
            ],
            wifi_signal=-30,
        )
        cache = status.DeviceStatusCache(
            status.DeviceState(status.CloudState(model="Argus 2E"), first)
        )
        notifications = []
        remove = cache.add_listener(lambda: notifications.append(True))
        cache.update_local(
            status.parse_local_state(
                refreshed_at=datetime(2026, 8, 14, 8, 5, tzinfo=UTC),
                device_info={},
                battery=None,
                storage=None,
                wifi_signal=None,
            )
        )
        merged = cache.state.local
        self.assertEqual(merged.device_info.firmware, "firmware-1")
        self.assertEqual(merged.storage.total_bytes, 100_000_000)
        self.assertEqual(merged.wifi_rssi_dbm, -30)
        self.assertEqual(len(notifications), 1)
        remove()
        cache.update_cloud(status.CloudState(status="bound"))
        self.assertEqual(len(notifications), 1)
        self.assertEqual(
            status.local_state_from_dict(status.local_state_as_dict(merged)), merged
        )
        self.assertIsNone(status.local_state_from_dict({"refreshed_at": "invalid"}))


if __name__ == "__main__":
    unittest.main()
