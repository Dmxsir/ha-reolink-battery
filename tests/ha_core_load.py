"""Smoke-test this integration against an installed Home Assistant Core."""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"


class FakeCoordinator:
    """No-network coordinator used only for the Core load smoke test."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.initialized = False
        self.stopped = False

    async def async_initialize(self) -> None:
        self.initialized = True

    async def async_run(self) -> None:
        return

    async def async_shutdown(self) -> None:
        self.stopped = True


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="reolink-ha-load-") as config_dir:
        component_target = (
            Path(config_dir) / "custom_components" / "reolink_battery"
        )
        shutil.copytree(COMPONENT, component_target)
        manifest_path = component_target / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # The network integration pulls in the HTTP frontend. It is unrelated
        # to entry/platform loading and is intentionally absent from this tiny
        # Core harness, so remove that dependency only from the temporary copy.
        manifest["dependencies"] = []
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        sys.path.insert(0, config_dir)

        hass = HomeAssistant(config_dir)
        loader.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()

        integration = importlib.import_module("custom_components.reolink_battery")
        const = importlib.import_module("custom_components.reolink_battery.const")
        status = importlib.import_module(
            "custom_components.reolink_battery.device_status"
        )
        integration.ReolinkBatteryCoordinator = FakeCoordinator
        integration.ReolinkCloudClient = lambda _session: object()
        integration.async_get_clientsession = lambda _hass: object()

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
        entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain=const.DOMAIN,
            title="Synthetic Argus",
            data={
                const.CONF_ACCOUNT_EMAIL: "account@example.invalid",
                const.CONF_ACCOUNT_PASSWORD: "unused",
                const.CONF_ACCESS_TOKEN: "",
                const.CONF_REFRESH_TOKEN: "",
                const.CONF_MFA_TRUST_TOKEN: "",
                const.CONF_USER_ID: "user-1",
                const.CONF_TOKEN_EXPIRES_AT: 0,
                const.CONF_UID: "synthetic-camera",
                const.CONF_DEVICE_NAME: "Synthetic Argus",
                const.CONF_MODEL: "Argus 2E",
                const.CONF_DEVICE_USERNAME: "admin",
                const.CONF_DEVICE_PASSWORD: "unused",
                const.CONF_INTERFACE: "192.0.2.61/24",
                const.CONF_LOCAL_STATE: status.local_state_as_dict(local),
            },
            options={},
            source="user",
            unique_id="synthetic-camera",
            discovery_keys=MappingProxyType({}),
            pref_disable_new_entities=None,
            pref_disable_polling=None,
        )
        # Register without ConfigEntries.async_add(), which would start unrelated
        # network/http dependencies in this deliberately minimal Core harness.
        hass.config_entries._entries[entry.entry_id] = entry
        entity_registry = er.async_get(hass)
        for key in integration.STORAGE_SENSOR_KEYS:
            registry_entry = entity_registry.async_get_or_create(
                "sensor",
                const.DOMAIN,
                f"synthetic-camera_{key}",
                config_entry=entry,
                suggested_object_id=f"synthetic_camera_{key}",
                unit_of_measurement="B",
            )
            entity_registry.async_update_entity_options(
                registry_entry.entity_id,
                "sensor.private",
                {"suggested_unit_of_measurement": "B"},
            )

        async with entry.setup_lock:
            assert await integration.async_setup_entry(hass, entry)
        entry._async_set_state(hass, ConfigEntryState.LOADED, None)
        await hass.async_block_till_done()
        entity_entries = er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        assert len(entity_entries) == 8
        entities_by_unique_id = {item.unique_id: item for item in entity_entries}
        battery_entity = entities_by_unique_id["synthetic-camera_battery"]
        charging_entity = entities_by_unique_id["synthetic-camera_charging"]
        storage_total_entity = entities_by_unique_id[
            "synthetic-camera_storage_total"
        ]
        storage_used_entity = entities_by_unique_id[
            "synthetic-camera_storage_used"
        ]
        storage_free_entity = entities_by_unique_id[
            "synthetic-camera_storage_free"
        ]
        assert "synthetic-camera_refresh_device_status" in entities_by_unique_id
        assert hass.states.get(battery_entity.entity_id).state == "100"
        assert hass.states.get(charging_entity.entity_id).state == "on"
        assert hass.states.get(storage_total_entity.entity_id).state == "14.857"
        assert hass.states.get(storage_used_entity.entity_id).state == "14.311"
        assert hass.states.get(storage_free_entity.entity_id).state == "0.546"
        assert hass.states.get(storage_total_entity.entity_id).attributes[
            "unit_of_measurement"
        ] == "GB"
        for key in integration.STORAGE_SENSOR_KEYS:
            registry_entry = entity_registry.async_get(
                entities_by_unique_id[f"synthetic-camera_{key}"].entity_id
            )
            assert registry_entry.unit_of_measurement == "GB"
            assert "sensor.private" not in registry_entry.options
        device_entries = dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        assert len(device_entries) == 1
        device = device_entries[0]
        assert device.model == "Reolink Argus 2E"
        assert device.sw_version == "0616_722_52_478"
        assert device.hw_version == "BIPC_36S7616MC94"
        print("HA_CORE_LOAD_OK=1")
        print("HA_CORE_ENTITY_COUNT=8")
        print("HA_CORE_DEVICE_INFO_OK=1")
        print("HA_CORE_LOCAL_CAMERA_CONTACTED=0")

        assert await integration.async_unload_entry(hass, entry)
        await hass.async_block_till_done()
        print("HA_CORE_UNLOAD_OK=1")
        entry.async_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
