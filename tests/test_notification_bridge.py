"""Milestone 3B.1 notification bridge tests; no phone, cloud, or camera is contacted."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_notification_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package

homeassistant = types.ModuleType("homeassistant")
core = types.ModuleType("homeassistant.core")
core.Event = object
core.HomeAssistant = object
core.State = object
core.callback = lambda func: func
helpers = types.ModuleType("homeassistant.helpers")
helper_event = types.ModuleType("homeassistant.helpers.event")
helper_event.async_track_state_change_event = lambda *_args, **_kwargs: lambda: None
sys.modules.update(
    {
        "homeassistant": homeassistant,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.event": helper_event,
    }
)


def load_module(name: str):
    full_name = f"{PACKAGE}.{name}"
    spec = importlib.util.spec_from_file_location(full_name, COMPONENT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


const = load_module("const")
events = load_module("events")
bridge = load_module("notification_bridge")

REAL_NOTIFICATION = {
    "package": "com.mcu.reolink",
    "android.title": "Camera Alert",
    "android.text": "An alarm from atv.",
    "android.bigText": "An alarm from atv.",
    "channel_id": "push.wav",
    "post_time": 1786709128188,
}


class ParserTests(unittest.TestCase):
    def parse(self, attributes=None, *, device_name="atv"):
        return bridge.notification_event_from_attributes(
            REAL_NOTIFICATION if attributes is None else attributes,
            expected_device_name=device_name,
            uid="camera-1",
        )

    def test_real_verified_notification(self):
        event = self.parse()
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.source, "android_notification")
        self.assertEqual(event.device_name, "atv")
        self.assertEqual(event.alarm_type, "MOTION")
        self.assertEqual(event.ai_types, ())
        self.assertEqual(event.title, "Camera Alert")
        self.assertEqual(event.text, "An alarm from atv.")
        self.assertEqual(event.notification_post_time.tzinfo, UTC)
        self.assertEqual(event.alarm_time, event.notification_post_time)

    def test_unrelated_package_is_ignored(self):
        attrs = {**REAL_NOTIFICATION, "package": "example.other"}
        self.assertIsNone(self.parse(attrs))

    def test_big_text_fallback(self):
        attrs = {
            **REAL_NOTIFICATION,
            "android.text": "",
            "android.bigText": "An alarm from atv.",
        }
        self.assertIsNotNone(self.parse(attrs))

    def test_missing_channel_is_allowed_for_known_template(self):
        attrs = dict(REAL_NOTIFICATION)
        attrs.pop("channel_id")
        self.assertIsNotNone(self.parse(attrs))

    def test_camera_name_mismatch_is_rejected(self):
        self.assertIsNone(self.parse(device_name="garden"))

    def test_localized_fallback_requires_camera_and_alarm_signal(self):
        attrs = {
            **REAL_NOTIFICATION,
            "android.text": "התראה מהמצלמה atv",
            "android.bigText": "",
        }
        self.assertIsNotNone(self.parse(attrs))
        attrs["android.title"] = "Something else"
        attrs["channel_id"] = "other"
        self.assertIsNone(self.parse(attrs))

    def test_post_time_string_is_converted_to_aware_utc(self):
        attrs = {**REAL_NOTIFICATION, "post_time": "1786709128188"}
        event = self.parse(attrs)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(
            event.notification_post_time,
            datetime.fromtimestamp(1786709128.188, UTC),
        )

    def test_invalid_post_time_is_rejected(self):
        self.assertIsNone(self.parse({**REAL_NOTIFICATION, "post_time": "bad"}))

    def test_deterministic_event_key_and_new_timestamp(self):
        first = self.parse()
        same = self.parse()
        newer = self.parse({**REAL_NOTIFICATION, "post_time": 1786709129188})
        assert first is not None and same is not None and newer is not None
        self.assertEqual(first.event_id, same.event_id)
        self.assertNotEqual(first.event_id, newer.event_id)

    def test_queue_deduplicates_repeated_notification(self):
        event = self.parse()
        assert event is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(event))
        self.assertFalse(queue.enqueue(event))
        self.assertEqual(len(queue.pending), 1)

    def test_notification_metadata_round_trips_storage(self):
        event = self.parse()
        assert event is not None
        restored = events.CloudEvent.from_storage(event.as_storage())
        self.assertEqual(restored, event)


class _FakeHass:
    def __init__(self):
        self.created_tasks = []

    def async_create_task(self, coroutine, _name):
        coroutine.close()
        self.created_tasks.append(True)


class RuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_once_then_duplicate_rejected(self):
        queue = events.EventQueue()

        async def ingest(event):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "atv", "camera-1", ingest
        )
        self.assertTrue(await listener.async_process_attributes(REAL_NOTIFICATION))
        self.assertTrue(listener.last_event_matched)
        self.assertTrue(listener.last_camera_mapped)
        self.assertTrue(listener.last_event_queued)
        self.assertFalse(listener.last_duplicate_rejected)
        self.assertFalse(await listener.async_process_attributes(REAL_NOTIFICATION))
        self.assertTrue(listener.last_event_matched)
        self.assertTrue(listener.last_camera_mapped)
        self.assertFalse(listener.last_event_queued)
        self.assertTrue(listener.last_duplicate_rejected)
        self.assertEqual(len(queue.pending), 1)

    async def test_non_reolink_notification_never_calls_ingest(self):
        calls = []

        async def ingest(event):
            calls.append(event)
            return 1

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "atv", "camera-1", ingest
        )
        attrs = {**REAL_NOTIFICATION, "package": "other.app"}
        self.assertFalse(await listener.async_process_attributes(attrs))
        self.assertEqual(calls, [])

    async def test_unrelated_notification_does_not_erase_last_reolink_telemetry(self):
        queue = events.EventQueue()

        async def ingest(event):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "atv", "camera-1", ingest
        )
        self.assertTrue(await listener.async_process_attributes(REAL_NOTIFICATION))
        saved_time = listener.last_reolink_notification_time
        self.assertFalse(
            await listener.async_process_attributes(
                {
                    "package": "com.example.other",
                    "android.title": "Other",
                    "android.text": "Not Reolink",
                    "post_time": 1786709130000,
                }
            )
        )
        self.assertTrue(listener.last_event_matched)
        self.assertTrue(listener.last_camera_mapped)
        self.assertTrue(listener.last_event_queued)
        self.assertFalse(listener.last_duplicate_rejected)
        self.assertEqual(listener.last_reolink_notification_time, saved_time)
        self.assertEqual(listener.last_reolink_notification_camera, "atv")

    async def test_beta29_tracks_sensor_updates_and_post_time_changes(self):
        queue = events.EventQueue()

        async def ingest(event):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "atv", "camera-1", ingest
        )
        self.assertTrue(await listener.async_process_attributes(REAL_NOTIFICATION))
        first_fingerprint = listener.last_event_fingerprint
        self.assertEqual(listener.sensor_state_change_count, 1)
        self.assertEqual(listener.matched_notification_update_count, 1)
        self.assertTrue(listener.last_post_time_changed)
        self.assertEqual(listener.last_notification_post_time_ms, 1786709128188)
        self.assertTrue(first_fingerprint)
        self.assertIsNotNone(listener.last_sensor_state_change_time)
        self.assertIsNotNone(listener.last_processing_lag_seconds)

        self.assertFalse(await listener.async_process_attributes(REAL_NOTIFICATION))
        self.assertEqual(listener.sensor_state_change_count, 2)
        self.assertEqual(listener.matched_notification_update_count, 2)
        self.assertFalse(listener.last_post_time_changed)
        self.assertEqual(listener.last_event_fingerprint, first_fingerprint)
        self.assertTrue(listener.last_duplicate_rejected)

        newer = {**REAL_NOTIFICATION, "post_time": 1786709129188}
        self.assertTrue(await listener.async_process_attributes(newer))
        self.assertEqual(listener.sensor_state_change_count, 3)
        self.assertEqual(listener.matched_notification_update_count, 3)
        self.assertTrue(listener.last_post_time_changed)
        self.assertEqual(listener.last_notification_post_time_ms, 1786709129188)
        self.assertNotEqual(listener.last_event_fingerprint, first_fingerprint)

    async def test_beta29_counts_unrelated_sensor_update_without_overwriting_match(self):
        queue = events.EventQueue()

        async def ingest(event):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "atv", "camera-1", ingest
        )
        self.assertTrue(await listener.async_process_attributes(REAL_NOTIFICATION))
        saved_post_time = listener.last_notification_post_time_ms
        self.assertFalse(
            await listener.async_process_attributes(
                {
                    "package": "com.example.other",
                    "android.title": "Other",
                    "android.text": "Not Reolink",
                    "post_time": 1786709130000,
                }
            )
        )
        self.assertEqual(listener.sensor_state_change_count, 2)
        self.assertEqual(listener.matched_notification_update_count, 1)
        self.assertEqual(listener.last_notification_post_time_ms, saved_post_time)

    async def test_pending_android_event_restores_runtime_telemetry_after_reload(self):
        event = bridge.notification_event_from_attributes(
            REAL_NOTIFICATION, expected_device_name="atv", uid="camera-1"
        )
        assert event is not None

        async def ingest(_event):
            return 0

        listener = bridge.NotificationBridge(
            _FakeHass(),
            "sensor.phone_last_notification",
            "atv",
            "camera-1",
            ingest,
            initial_event=event,
        )
        self.assertTrue(listener.last_event_matched)
        self.assertTrue(listener.last_camera_mapped)
        self.assertTrue(listener.last_event_queued)
        self.assertFalse(listener.last_duplicate_rejected)
        self.assertEqual(listener.last_reolink_notification_time, event.notification_post_time)
        self.assertEqual(listener.last_reolink_notification_camera, "atv")


if __name__ == "__main__":
    unittest.main()
