"""Synthetic Home Assistant setup/unload test; no network is contacted."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_load_test"


class _Value(StrEnum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    CAMERA = "camera"
    BATTERY = "battery"
    BATTERY_CHARGING = "battery_charging"
    DATA_SIZE = "data_size"
    SIGNAL_STRENGTH = "signal_strength"
    TIMESTAMP = "timestamp"
    MEASUREMENT = "measurement"
    DIAGNOSTIC = "diagnostic"
    BYTES = "B"
    GIGABYTES = "GB"
    STREAM = "stream"


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


@dataclass
class _CloudTokens:
    access_token: str = ""
    refresh_token: str = ""
    mfa_trust_token: str = ""
    user_id: str = ""
    expires_at: float = 0


class _FakeCoordinator:
    pending_seed: ClassVar[tuple] = ()
    deferred_seed: ClassVar[dict[str, object]] = {}

    def __init__(self, *_args, **_kwargs):
        self.pending_events = tuple(self.pending_seed)
        self._deferred = dict(self.deferred_seed)
        self.completed_recording_fingerprints = frozenset()
        self.stopped = False

    @property
    def deferred_event_count(self):
        return len(self._deferred)

    @property
    def deferred_event_ids(self):
        return frozenset(self._deferred)

    @property
    def last_deferred_event(self):
        return max(
            self._deferred.values(),
            key=lambda item: item.deferred_at,
            default=None,
        )

    def is_event_deferred(self, event_id):
        return event_id in self._deferred

    async def async_initialize(self):
        return None

    async def async_run(self):
        return None

    async def async_shutdown(self):
        self.stopped = True

    async def async_ingest_events(self, incoming):
        existing = {event.event_id for event in self.pending_events}
        accepted = [event for event in incoming if event.event_id not in existing]
        self.pending_events += tuple(accepted)
        return len(accepted)

    async def async_defer_event(self, event_id, reason, *, deferred_at=None):
        if event_id not in {event.event_id for event in self.pending_events}:
            return False
        self._deferred[event_id] = types.SimpleNamespace(
            event_id=event_id,
            reason=reason,
            deferred_at=deferred_at or datetime.now(UTC),
        )
        return True

    async def async_complete_event(self, event_id, *, completed_recording=None):
        before = len(self.pending_events)
        self.pending_events = tuple(
            event for event in self.pending_events if event.event_id != event_id
        )
        self._deferred.pop(event_id, None)
        return len(self.pending_events) != before


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
    core.Event = object
    core.State = object
    core.callback = lambda func: func
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
    helper_event = types.ModuleType("homeassistant.helpers.event")
    helper_event.async_track_state_change_event = (
        lambda *_args, **_kwargs: lambda: None
    )
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
            "homeassistant.helpers.event": helper_event,
            "homeassistant.exceptions": exceptions,
        }
    )

    cloud = types.ModuleType(f"{PACKAGE}.cloud")
    cloud.CloudTokens = _CloudTokens
    cloud.ReolinkCloudClient = lambda _session: object()
    coordinator = types.ModuleType(f"{PACKAGE}.coordinator")
    coordinator.ReolinkBatteryCoordinator = _FakeCoordinator

    class _LiveHub:
        is_active = False

        def __init__(self, _hass, _entry):
            pass

        async def async_stop(self):
            return None

    live_http = types.ModuleType(f"{PACKAGE}.live_http")
    live_http.ReolinkBatteryLiveHub = _LiveHub
    live_http.ReolinkBatteryAacView = type("ReolinkBatteryAacView", (), {})
    live_http.ReolinkBatteryH264View = type("ReolinkBatteryH264View", (), {})

    async def async_ensure_go2rtc_bridge(_hass, _entry):
        return types.SimpleNamespace(rtsp_url=None)

    go2rtc = types.ModuleType(f"{PACKAGE}.go2rtc_bridge")
    go2rtc.async_ensure_go2rtc_bridge = async_ensure_go2rtc_bridge

    async def async_not_available(*_args, **_kwargs):
        raise AssertionError("recording protocol must not run during setup")

    def unused_state(*_args, **_kwargs):
        return types.SimpleNamespace()

    recording_download = types.ModuleType(
        f"{PACKAGE}.recording_download_beta22"
    )
    recording_download.apply_file_info_trace = lambda *_args: None
    recording_download.apply_identity_trace = lambda *_args: None
    recording_download.apply_stream_probe_trace = lambda *_args: None
    recording_download.async_prepare_download_for_event = async_not_available
    recording_download.download_prepare_state = unused_state
    recording_download.reset_stream_probe_state = lambda *_args: None

    recording_probe = types.ModuleType(f"{PACKAGE}.recording_probe")
    recording_probe.async_find_recording_for_event = async_not_available
    recording_probe.probe_state = unused_state

    class _SyntheticCamera(_Entity):
        _attr_should_poll = False

        def __init__(self, entry):
            self._attr_unique_id = f"{entry.data['uid']}_live_view"

    async def async_setup_camera(_hass, entry, async_add_entities):
        async_add_entities((_SyntheticCamera(entry),))

    class CameraStageError(RuntimeError):
        def __init__(self, stage):
            super().__init__(stage)
            self.stage = stage

    async def async_validate_legacy_device(*_args, **_kwargs):
        camera.local_session_open_count += 1
        raise AssertionError("setup must not open a local camera session")

    camera = types.ModuleType(f"{PACKAGE}.camera")
    camera.CameraStageError = CameraStageError
    camera.async_setup_entry = async_setup_camera
    camera.async_validate_legacy_device = async_validate_legacy_device
    camera.local_session_open_count = 0
    sys.modules.update(
        {
            f"{PACKAGE}.cloud": cloud,
            f"{PACKAGE}.coordinator": coordinator,
            f"{PACKAGE}.live_http": live_http,
            f"{PACKAGE}.go2rtc_bridge": go2rtc,
            f"{PACKAGE}.recording_download_beta22": recording_download,
            f"{PACKAGE}.recording_probe": recording_probe,
            f"{PACKAGE}.camera": camera,
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
event_model = importlib.import_module(f"{PACKAGE}.events")


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
        self.data = {}
        self.http = types.SimpleNamespace(register_view=lambda _view: None)


class _Entry(_ConfigEntry):
    def __init__(self, hass, data, *, options=None):
        self.hass = hass
        self.entry_id = "entry-1"
        self.title = "Synthetic Argus"
        self.data = data
        self.options = options or {}
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

    def _entry(self, hass, local=None, *, notification_bridge=False):
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
        options = (
            {const.CONF_NOTIFICATION_ENTITY: "sensor.phone_last_notification"}
            if notification_bridge
            else {}
        )
        return _Entry(hass, data, options=options)

    @staticmethod
    def _android_event(event_id, event_time):
        return event_model.CloudEvent(
            event_id=event_id,
            uid="camera-1",
            alarm_time=event_time,
            alarm_type="MOTION",
            source="android_notification",
            device_name="Front camera",
            notification_post_time=event_time,
        )

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
        install_stubs()
        _FakeCoordinator.pending_seed = ()
        _FakeCoordinator.deferred_seed = {}

    async def test_setup_and_unload_restore_cache_without_camera_polling(self):
        hass = _Hass()
        local = self._local_state()
        entry = self._entry(hass, local)
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
            (_Value.SENSOR, _Value.BINARY_SENSOR, _Value.BUTTON, _Value.CAMERA),
        )
        self.assertEqual(len(hass.config_entries.entities), 11)
        self.assertEqual(entry.runtime_data.status.state.local, local)
        self.assertTrue(
            all(
                registry_entry.unit_of_measurement == "GB"
                and registry_entry.options["sensor.private"] == {"keep": True}
                for registry_entry in ENTITY_REGISTRY.entries.values()
            )
        )
        self.assertEqual(
            sys.modules[f"{PACKAGE}.camera"].local_session_open_count,
            0,
        )
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
            (_Value.SENSOR, _Value.BINARY_SENSOR, _Value.BUTTON, _Value.CAMERA),
        )

    async def test_setup_skips_stale_and_deferred_android_backlog(self):
        now = datetime.now(UTC)
        deferred = self._android_event("deferred", now - timedelta(minutes=2))
        stale = self._android_event("stale", now - timedelta(minutes=20))
        _FakeCoordinator.pending_seed = (deferred, stale)
        _FakeCoordinator.deferred_seed = {
            deferred.event_id: types.SimpleNamespace(
                event_id=deferred.event_id,
                reason="automatic_retries_exhausted",
                deferred_at=now - timedelta(minutes=1),
            )
        }
        hass = _Hass()
        entry = self._entry(hass, notification_bridge=True)

        self.assertTrue(await integration.async_setup_entry(hass, entry))

        worker = entry.runtime_data.recording_worker
        self.assertIsNotNone(worker)
        self.assertFalse(worker.state.pending_trigger)
        self.assertEqual(worker.state.attempts, 0)
        self.assertEqual(
            entry.runtime_data.coordinator.deferred_event_ids,
            frozenset({deferred.event_id, stale.event_id}),
        )
        self.assertEqual(
            sys.modules[f"{PACKAGE}.camera"].local_session_open_count,
            0,
        )

    async def test_setup_activates_one_fresh_startup_recovery_without_camera(self):
        now = datetime.now(UTC)
        fresh = self._android_event("fresh", now - timedelta(seconds=30))
        _FakeCoordinator.pending_seed = (fresh,)
        hass = _Hass()
        entry = self._entry(hass, notification_bridge=True)

        self.assertTrue(await integration.async_setup_entry(hass, entry))

        worker = entry.runtime_data.recording_worker
        self.assertIsNotNone(worker)
        self.assertTrue(worker.state.pending_trigger)
        self.assertEqual(worker._activated_event_ids, {fresh.event_id})
        self.assertEqual(worker.state.attempts, 0)
        self.assertEqual(
            sys.modules[f"{PACKAGE}.camera"].local_session_open_count,
            0,
        )

    async def test_successful_press_updates_cache_and_entities_immediately(self):
        hass = _Hass()
        entry = self._entry(hass, self._local_state())
        await integration.async_setup_entry(hass, entry)
        state_entities = [
            entity
            for entity in hass.config_entries.entities
            if entity.__class__.__module__.endswith(
                (".sensor", ".binary_sensor")
            )
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
