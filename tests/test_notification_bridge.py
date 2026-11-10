"""Milestone 3B.1 notification bridge tests; no phone, cloud, or camera is contacted."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
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
    "android.text": "An alarm from porch.",
    "android.bigText": "An alarm from porch.",
    "channel_id": "push.wav",
    "post_time": 1786700000188,
}


def _active_notification_record(
    notification_id,
    post_time,
    *,
    package="com.mcu.reolink",
    rich=True,
):
    extra_suffix = f"_{package}_{notification_id}"
    metadata_prefix = f"{package}_{notification_id}_"
    attributes = {
        f"{metadata_prefix}post_time": post_time,
        f"{metadata_prefix}channel_id": "push.wav",
        f"{metadata_prefix}group_id": "private group",
        f"{metadata_prefix}category": "private category",
    }
    if rich:
        attributes.update(
            {
                f"android.title{extra_suffix}": "Camera Alert",
                f"android.text{extra_suffix}": "An alarm from porch.",
                f"android.bigText{extra_suffix}": "An alarm from porch.",
            }
        )
    return attributes


class ParserTests(unittest.TestCase):
    def parse(self, attributes=None, *, device_name="porch"):
        return bridge.notification_event_from_attributes(
            REAL_NOTIFICATION if attributes is None else attributes,
            expected_device_name=device_name,
            uid="camera-1",
        )

    def decision(self, attributes=None, *, device_name="porch"):
        return bridge._notification_event_from_attributes_with_decision(
            REAL_NOTIFICATION if attributes is None else attributes,
            expected_device_name=device_name,
            uid="camera-1",
        )

    def assert_rejected(self, attributes, reason, *, device_name="porch"):
        event, decision = self.decision(attributes, device_name=device_name)
        self.assertIsNone(event)
        self.assertFalse(decision["normalized"])
        self.assertEqual(decision["rejection_reason"], reason)
        return decision

    def test_real_verified_notification(self):
        event = self.parse()
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.source, "android_notification")
        self.assertEqual(event.device_name, "porch")
        self.assertEqual(event.alarm_type, "MOTION")
        self.assertEqual(event.ai_types, ())
        self.assertEqual(event.title, "Camera Alert")
        self.assertEqual(event.text, "An alarm from porch.")
        self.assertEqual(event.notification_post_time.tzinfo, UTC)
        self.assertEqual(event.alarm_time, event.notification_post_time)

    def test_unrelated_package_is_ignored(self):
        attrs = {**REAL_NOTIFICATION, "package": "example.other"}
        self.assertIsNone(self.parse(attrs))

    def test_big_text_fallback(self):
        attrs = {
            **REAL_NOTIFICATION,
            "android.text": "",
            "android.bigText": "An alarm from porch.",
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
            "android.text": "התראה מהמצלמה porch",
            "android.bigText": "",
        }
        self.assertIsNotNone(self.parse(attrs))
        attrs["android.title"] = "Something else"
        attrs["channel_id"] = "other"
        self.assertIsNone(self.parse(attrs))

    def test_post_time_string_is_converted_to_aware_utc(self):
        attrs = {**REAL_NOTIFICATION, "post_time": "1786700000188"}
        event = self.parse(attrs)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(
            event.notification_post_time,
            datetime.fromtimestamp(1786700000.188, UTC),
        )

    def test_invalid_post_time_is_rejected(self):
        self.assertIsNone(self.parse({**REAL_NOTIFICATION, "post_time": "bad"}))

    def test_diagnostic_reason_for_every_parser_rejection_path(self):
        missing_package = dict(REAL_NOTIFICATION)
        missing_package.pop("package")
        self.assert_rejected(missing_package, "package_missing")
        self.assert_rejected(
            {**REAL_NOTIFICATION, "package": "other.app"},
            "package_not_reolink",
        )
        self.assert_rejected(
            {**REAL_NOTIFICATION, "android.text": "", "android.bigText": ""},
            "notification_text_missing",
        )
        self.assert_rejected(
            REAL_NOTIFICATION,
            "camera_mismatch",
            device_name="garden",
        )
        self.assert_rejected(
            {
                **REAL_NOTIFICATION,
                "android.title": "Other",
                "android.text": "Motion near porch",
                "android.bigText": "",
                "channel_id": "other",
            },
            "fallback_alarm_signature_failed",
        )
        self.assert_rejected(
            {
                **REAL_NOTIFICATION,
                "android.text": "Motion near garden",
                "android.bigText": "",
            },
            "configured_camera_absent_from_fallback",
        )
        missing_post_time = dict(REAL_NOTIFICATION)
        missing_post_time.pop("post_time")
        self.assert_rejected(missing_post_time, "post_time_missing")
        wrong_type = self.assert_rejected(
            {**REAL_NOTIFICATION, "post_time": []},
            "post_time_wrong_type",
        )
        self.assertTrue(wrong_type["post_time_present"])
        self.assertFalse(wrong_type["post_time_type_valid"])
        self.assert_rejected(
            {**REAL_NOTIFICATION, "post_time": "bad"},
            "post_time_parse_failed",
        )
        self.assert_rejected(
            {**REAL_NOTIFICATION, "post_time": float("inf")},
            "post_time_parse_failed",
        )

    def test_valid_normalization_decision(self):
        event, decision = self.decision()
        self.assertIsNotNone(event)
        assert event is not None
        self.assertTrue(decision["normalized"])
        self.assertEqual(decision["rejection_reason"], "normalized")
        self.assertTrue(decision["package_is_reolink"])
        self.assertTrue(decision["notification_text_present"])
        self.assertTrue(decision["big_text_present"])
        self.assertTrue(decision["camera_extracted"])
        self.assertTrue(decision["camera_match"])
        self.assertFalse(decision["fallback_path_used"])
        self.assertTrue(decision["post_time_present"])
        self.assertTrue(decision["post_time_type_valid"])
        self.assertTrue(decision["post_time_parse_valid"])
        self.assertEqual(
            decision["candidate_fingerprint"],
            event.event_id.removeprefix("android:")[:12],
        )

    def test_deterministic_event_key_and_new_timestamp(self):
        first = self.parse()
        same = self.parse()
        newer = self.parse({**REAL_NOTIFICATION, "post_time": 1786700001188})
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


class _AsyncFakeHass:
    def __init__(self):
        self.created_tasks = []

    def async_create_task(self, coroutine, _name):
        task = asyncio.create_task(coroutine)
        self.created_tasks.append(task)
        return task


def _state(attributes, state="notification"):
    return types.SimpleNamespace(attributes=attributes, state=state)


def _state_event(old_state, new_state):
    return types.SimpleNamespace(
        data={"old_state": old_state, "new_state": new_state}
    )


class IdentityObservationTests(unittest.TestCase):
    def listener(self, ingest=None):
        async def default_ingest(_event, _allow_automatic_wake=True):
            return 0

        return bridge.NotificationBridge(
            _FakeHass(),
            "sensor.phone_last_notification",
            "porch",
            "camera-1",
            ingest or default_ingest,
            identity_entity_id="sensor.phone_active_notification_count",
        )

    def observe(self, listener, attributes):
        listener._handle_identity_state_change(
            _state_event(None, _state(attributes))
        )

    def test_stable_and_differing_numeric_ids_remain_distinct(self):
        listener = self.listener()
        self.observe(listener, _active_notification_record(42, 1786700002000))
        self.observe(
            listener,
            _active_notification_record(42, 1786700003000, rich=False),
        )
        self.observe(listener, _active_notification_record(43, 1786700004000))

        history = list(listener.recent_notification_identity_observations)
        self.assertEqual([item["notification_id"] for item in history], [42, 42, 43])
        self.assertEqual(
            history[0]["notification_identity_hash"],
            history[1]["notification_identity_hash"],
        )
        self.assertNotEqual(
            history[1]["notification_identity_hash"],
            history[2]["notification_identity_hash"],
        )
        self.assertTrue(history[0]["normalized"])
        self.assertFalse(history[1]["normalized"])
        self.assertTrue(history[0]["camera_match"])
        self.assertFalse(history[1]["camera_match"])

    def test_history_is_oldest_first_and_bounded_to_sixteen(self):
        listener = self.listener()
        for notification_id in range(17):
            self.observe(
                listener,
                _active_notification_record(
                    notification_id,
                    1786700002000 + notification_id,
                    rich=False,
                ),
            )

        history = list(listener.recent_notification_identity_observations)
        self.assertEqual(listener.recent_notification_identity_observations.maxlen, 16)
        self.assertEqual(len(history), 16)
        self.assertEqual(
            [item["notification_id"] for item in history],
            list(range(1, 17)),
        )

    def test_exact_post_time_relation_is_recorded_without_proximity_matching(self):
        listener = self.listener()
        post_time = 1786700002000
        self.observe(listener, _active_notification_record(42, post_time))
        observation = listener.recent_notification_identity_observations[-1]
        self.assertEqual(observation["last_notification_relation"], "not_available")

        listener._handle_state_change(
            _state_event(
                None,
                _state({**REAL_NOTIFICATION, "post_time": post_time}),
            )
        )
        self.assertEqual(
            observation["last_notification_relation"],
            "exact_post_time_match",
        )

        self.observe(listener, _active_notification_record(43, post_time + 1))
        self.assertEqual(
            listener.recent_notification_identity_observations[-1][
                "last_notification_relation"
            ],
            "different_post_time",
        )

    def test_missing_id_and_unrelated_package_are_secret_safe(self):
        listener = self.listener()
        self.observe(
            listener,
            {
                "android.title_com.mcu.reolink_unknown": "Private Reolink title",
                "android.text_com.example.private_9": "Private unrelated text",
                "com.example.private_9_post_time": 1786700002000,
            },
        )
        observation = listener.recent_notification_identity_observations[-1]
        self.assertTrue(observation["package_is_reolink"])
        self.assertFalse(observation["notification_id_present"])
        self.assertEqual(observation["notification_id"], None)
        self.assertFalse(observation["notification_key_present"])
        self.assertFalse(observation["tag_present"])
        self.assertFalse(observation["group_key_present"])

        serialized = json.dumps(observation, sort_keys=True)
        for raw_value in (
            "com.mcu.reolink",
            "Private Reolink title",
            "com.example.private",
            "Private unrelated text",
        ):
            self.assertNotIn(raw_value, serialized)

    def test_identity_observation_history_stays_secret_safe(self):
        listener = self.listener()
        self.observe(listener, _active_notification_record(42, 1786700002000))

        self.assertIsNone(
            bridge.notification_event_from_attributes(
                {
                    "package": "com.mcu.reolink",
                    "channel_id": "push.wav",
                    "post_time": 1786700002000,
                },
                expected_device_name="porch",
                uid="camera-1",
            )
        )

        serialized = json.dumps(
            list(listener.recent_notification_identity_observations),
            sort_keys=True,
        )
        for raw_value in (
            "Camera Alert",
            "An alarm from porch.",
            "porch",
            "private group",
            "private category",
            "push.wav",
        ):
            self.assertNotIn(raw_value, serialized)

    def test_optional_identity_listener_is_bound_and_removed_separately(self):
        tracked = []
        removed = []

        def track(_hass, entity_ids, handler):
            tracked.append((entity_ids, handler))
            return lambda: removed.append(entity_ids)

        original = bridge.async_track_state_change_event
        bridge.async_track_state_change_event = track
        try:
            listener = self.listener()
            listener.start()
            listener.start()
            self.assertEqual(
                [entity_ids for entity_ids, _handler in tracked],
                [
                    ["sensor.phone_last_notification"],
                    ["sensor.phone_active_notification_count"],
                ],
            )
            self.assertTrue(listener.active)
            self.assertTrue(listener.identity_listener_active)

            listener.stop()
            self.assertEqual(
                removed,
                [
                    ["sensor.phone_last_notification"],
                    ["sensor.phone_active_notification_count"],
                ],
            )
            self.assertFalse(listener.active)
            self.assertFalse(listener.identity_listener_active)
        finally:
            bridge.async_track_state_change_event = original


GROUP_SUMMARY = {
    # Shape observed on the phone: Android's automatic group summary carries no
    # title or text, only the Reolink package, channel and its own post_time.
    "package": "com.mcu.reolink",
    "channel_id": "push.wav",
    "group_id": "0|com.mcu.reolink|g:Aggregate_AlertingSection",
}


class IdentityIngestionTests(unittest.IsolatedAsyncioTestCase):
    """Alarms hidden from Last Notification are queued from the identity sensor."""

    def setUp(self):
        self.queue = events.EventQueue()
        self.queued = []
        self.hass = _AsyncFakeHass()
        self.now_ms = round(datetime.now(UTC).timestamp() * 1000)

    async def ingest(self, event, allow_automatic_wake=True):
        # Mirrors the integration callback: the persistent queue decides.
        if not self.queue.enqueue(event):
            return 0
        self.queued.append((event, allow_automatic_wake))
        return 1

    def listener(self):
        return bridge.NotificationBridge(
            self.hass,
            "sensor.phone_last_notification",
            "porch",
            "camera-1",
            self.ingest,
            identity_entity_id="sensor.phone_active_notification_count",
        )

    async def last_notification(self, listener, attributes, old=None):
        listener._handle_state_change(
            _state_event(
                None if old is None else _state(old),
                _state(attributes),
            )
        )
        await asyncio.gather(*self.hass.created_tasks)

    async def identity(self, listener, attributes):
        listener._handle_identity_state_change(
            _state_event(None, _state(attributes))
        )
        await asyncio.gather(*self.hass.created_tasks)

    async def test_alarm_hidden_behind_group_summary_is_queued(self):
        listener = self.listener()
        alarm_post_time = self.now_ms - 1000
        # The summary is posted a few milliseconds after the alarm itself.
        summary = {**GROUP_SUMMARY, "post_time": alarm_post_time + 22}

        await self.last_notification(listener, summary)
        self.assertEqual(self.queued, [])
        self.assertEqual(
            listener.last_callback_decision["rejection_reason"],
            "notification_text_missing",
        )
        self.assertEqual(listener.textless_reolink_notification_count, 1)

        await self.identity(
            listener, _active_notification_record(0, alarm_post_time)
        )

        self.assertEqual(len(self.queued), 1)
        event, allow_automatic_wake = self.queued[0]
        self.assertTrue(allow_automatic_wake)
        expected = bridge.notification_event_from_attributes(
            {**REAL_NOTIFICATION, "post_time": alarm_post_time},
            expected_device_name="porch",
            uid="camera-1",
        )
        self.assertEqual(event, expected)
        self.assertEqual(event.source, "android_notification")
        self.assertEqual(listener.identity_candidate_count, 1)
        self.assertEqual(listener.identity_recovered_event_count, 1)
        self.assertEqual(listener.identity_duplicate_count, 0)
        self.assertTrue(listener.last_identity_recovered_wake_permission)
        self.assertTrue(listener.last_event_queued)
        self.assertFalse(listener.last_duplicate_rejected)
        self.assertEqual(
            listener.last_reolink_notification_time, event.notification_post_time
        )
        self.assertEqual(
            listener.last_effective_event_time_source, "active_notification_count"
        )

    async def test_alarm_delivered_by_both_sensors_is_queued_once(self):
        listener = self.listener()
        post_time = self.now_ms - 1000

        await self.last_notification(
            listener, {**REAL_NOTIFICATION, "post_time": post_time}
        )
        await self.identity(listener, _active_notification_record(0, post_time))

        self.assertEqual(len(self.queued), 1)
        self.assertEqual(len(self.queue.pending), 1)
        self.assertEqual(listener.identity_candidate_count, 0)
        self.assertEqual(listener.identity_recovered_event_count, 0)
        self.assertEqual(listener.textless_reolink_notification_count, 0)

    async def test_identity_first_then_last_notification_is_queued_once(self):
        listener = self.listener()
        post_time = self.now_ms - 1000

        await self.identity(listener, _active_notification_record(0, post_time))
        await self.last_notification(
            listener, {**REAL_NOTIFICATION, "post_time": post_time}
        )

        self.assertEqual(len(self.queued), 1)
        self.assertEqual(len(self.queue.pending), 1)
        self.assertEqual(listener.identity_recovered_event_count, 1)
        self.assertTrue(listener.last_duplicate_rejected)

    async def test_identity_record_is_offered_once_across_updates(self):
        listener = self.listener()
        record = _active_notification_record(0, self.now_ms - 1000)

        for _ in range(3):
            await self.identity(listener, record)

        self.assertEqual(len(self.queued), 1)
        self.assertEqual(listener.identity_candidate_count, 1)
        self.assertEqual(len(self.hass.created_tasks), 1)

    async def test_processed_alarm_is_not_requeued_after_restart(self):
        record = _active_notification_record(0, self.now_ms - 1000)
        await self.identity(self.listener(), record)
        self.assertEqual(len(self.queued), 1)
        self.queue.remove(self.queued[0][0].event_id)

        restarted = self.listener()
        await self.identity(restarted, record)

        self.assertEqual(len(self.queued), 1)
        self.assertEqual(self.queue.pending, ())
        self.assertEqual(restarted.identity_candidate_count, 1)
        self.assertEqual(restarted.identity_duplicate_count, 1)
        self.assertEqual(restarted.identity_recovered_event_count, 0)
        self.assertFalse(restarted.last_event_queued)

    async def test_stale_identity_alarm_is_queued_without_wake_permission(self):
        listener = self.listener()
        stale = self.now_ms - round(
            (const.AUTOMATIC_RECORDING_EVENT_MAX_AGE + timedelta(minutes=1))
            .total_seconds()
            * 1000
        )

        await self.identity(listener, _active_notification_record(0, stale))

        self.assertEqual(len(self.queued), 1)
        self.assertFalse(self.queued[0][1])
        self.assertFalse(listener.last_identity_recovered_wake_permission)

    async def test_two_hidden_alarms_are_both_queued(self):
        listener = self.listener()
        first = self.now_ms - 60_000
        second = self.now_ms - 1000

        await self.identity(listener, _active_notification_record(0, first))
        await self.identity(listener, _active_notification_record(0, second))

        self.assertEqual(
            [
                round(event.notification_post_time.timestamp() * 1000)
                for event, _wake in self.queued
            ],
            [first, second],
        )
        self.assertEqual(listener.identity_recovered_event_count, 2)

    async def test_other_camera_textless_and_foreign_records_are_ignored(self):
        listener = self.listener()
        post_time = self.now_ms - 1000
        other_camera = _active_notification_record(1, post_time)
        other_camera["android.text_com.mcu.reolink_1"] = "An alarm from garage."
        other_camera["android.bigText_com.mcu.reolink_1"] = "An alarm from garage."
        attributes = {
            **other_camera,
            **_active_notification_record(2, post_time + 1, rich=False),
            **_active_notification_record(
                3, post_time + 2, package="com.example.private"
            ),
        }

        await self.identity(listener, attributes)

        self.assertEqual(self.queued, [])
        self.assertEqual(self.hass.created_tasks, [])
        self.assertEqual(listener.identity_candidate_count, 0)

    async def test_textless_counter_counts_each_summary_once(self):
        listener = self.listener()
        summary = {**GROUP_SUMMARY, "post_time": self.now_ms - 1000}

        await self.last_notification(listener, summary)
        # The Companion app periodically re-sends the unchanged sensor state.
        await self.last_notification(listener, summary, old=summary)
        self.assertEqual(listener.textless_reolink_notification_count, 1)

        await self.last_notification(
            listener, {**GROUP_SUMMARY, "post_time": self.now_ms}, old=summary
        )
        self.assertEqual(listener.textless_reolink_notification_count, 2)

        await self.last_notification(
            listener,
            {"package": "com.example.private", "post_time": self.now_ms + 1},
        )
        self.assertEqual(listener.textless_reolink_notification_count, 2)
        self.assertEqual(self.queued, [])


class RuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_callback_change_classifications(self):
        async def ingest(_event, _allow_automatic_wake=True):
            return 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        unrelated = {
            "package": "other.app",
            "android.title": "Other",
            "android.text": "Not Reolink",
            "post_time": 1786700002000,
        }
        changed_title = {**unrelated, "android.title": "Changed"}
        cases = (
            (None, _state(unrelated), "created", list(unrelated)),
            (
                _state(unrelated, "old"),
                _state(unrelated, "new"),
                "state_only",
                [],
            ),
            (
                _state(unrelated),
                _state(changed_title),
                "attributes_only",
                ["android.title"],
            ),
            (
                _state(unrelated, "old"),
                _state(changed_title, "new"),
                "state_and_attributes",
                ["android.title"],
            ),
            (
                _state(unrelated),
                _state(unrelated),
                "force_update_unchanged",
                [],
            ),
        )
        for old_state, new_state, classification, changed_fields in cases:
            listener._handle_state_change(_state_event(old_state, new_state))
            await hass.created_tasks[-1]
            decision = listener.last_callback_decision
            self.assertEqual(decision["change_classification"], classification)
            self.assertEqual(
                decision["changed_relevant_fields"],
                [
                    key
                    for key in bridge._RELEVANT_ATTRIBUTE_KEYS
                    if key in changed_fields
                ],
            )
            if classification == "state_only":
                self.assertEqual(
                    decision["old_relevant_attributes_hash"],
                    decision["new_relevant_attributes_hash"],
                )
            if classification in {"attributes_only", "state_and_attributes"}:
                self.assertNotEqual(
                    decision["old_relevant_attributes_hash"],
                    decision["new_relevant_attributes_hash"],
                )

        listener._handle_state_change(_state_event(_state(unrelated), None))
        self.assertEqual(
            listener.last_callback_decision["change_classification"], "unknown"
        )
        self.assertEqual(
            listener.last_callback_decision["rejection_reason"],
            "new_state_missing",
        )

    async def test_last_callback_decision_for_accepted_callback_is_current(self):
        queue = events.EventQueue()

        async def ingest(event, _allow_automatic_wake=True):
            return 1 if queue.enqueue(event) else 0

        observed_at = datetime.now(UTC)
        attributes = {
            **REAL_NOTIFICATION,
            "post_time": round(observed_at.timestamp() * 1000),
        }
        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        listener._handle_state_change(_state_event(None, _state(attributes)))
        await hass.created_tasks[-1]

        decision = listener.last_callback_decision
        self.assertEqual(decision["scope"], "latest_home_assistant_sensor_callback")
        self.assertEqual(decision["change_classification"], "created")
        self.assertFalse(decision["old_state_existed"])
        self.assertTrue(decision["normalized"])
        self.assertEqual(decision["rejection_reason"], "normalized")
        self.assertTrue(decision["candidate_fingerprint"])
        self.assertTrue(decision["post_time_changed"])
        self.assertTrue(decision["stale_repost_evaluation_reached"])
        self.assertTrue(decision["source_wake_permission"])
        self.assertFalse(decision["source_duplicate_rejected"])
        self.assertFalse(decision["duplicate_rejected"])
        self.assertEqual(decision["new_post_time_ms"], attributes["post_time"])
        self.assertEqual(decision["source_queue_result"], "queued")
        self.assertEqual(decision["queue_result"], "queued")
        serialized = json.dumps(decision, sort_keys=True)
        for raw_value in (
            "com.mcu.reolink",
            "Camera Alert",
            "An alarm from porch.",
            "porch",
        ):
            self.assertNotIn(raw_value, serialized)

    async def test_recent_callback_history_keeps_reolink_before_unrelated(self):
        async def ingest(_event, _allow_automatic_wake=True):
            return 1

        observed_at = datetime.now(UTC)
        accepted = {
            **REAL_NOTIFICATION,
            "post_time": round(observed_at.timestamp() * 1000),
        }
        unrelated = {
            "package": "com.example.private",
            "android.title": "Private reminder",
            "android.text": "Private message",
            "android.bigText": "Private expanded message",
            "post_time": accepted["post_time"] + 1,
        }
        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )

        listener._handle_state_change(_state_event(None, _state(accepted)))
        await hass.created_tasks[-1]
        accepted_decision = listener.recent_callback_decisions[-1]
        self.assertTrue(accepted_decision["normalized"])
        self.assertIs(listener.last_callback_decision, accepted_decision)

        listener._handle_state_change(
            _state_event(_state(accepted), _state(unrelated))
        )
        await hass.created_tasks[-1]

        history = list(listener.recent_callback_decisions)
        self.assertEqual(len(history), 2)
        self.assertIs(history[0], accepted_decision)
        self.assertIsNot(history[0], history[1])
        required_fields = {
            "callback_time",
            "change_classification",
            "old_state_existed",
            "old_relevant_attributes_hash",
            "new_relevant_attributes_hash",
            "changed_relevant_fields",
            "old_post_time_ms",
            "new_post_time_ms",
            "package_is_reolink",
            "notification_text_present",
            "big_text_present",
            "camera_extracted",
            "camera_match",
            "fallback_path_used",
            "post_time_present",
            "post_time_type_valid",
            "post_time_parse_valid",
            "normalized",
            "rejection_reason",
            "candidate_fingerprint",
            "post_time_changed",
            "stale_repost_evaluation_reached",
            "stale_repost_decision",
            "source_wake_permission",
            "duplicate_rejected",
            "queue_result",
        }
        for entry in history:
            self.assertLessEqual(required_fields, set(entry))
        self.assertEqual(set(history[0]), set(history[1]))
        self.assertTrue(history[0]["normalized"])
        self.assertFalse(history[1]["normalized"])
        self.assertEqual(history[1]["rejection_reason"], "package_not_reolink")
        self.assertEqual(history[0]["new_post_time_ms"], accepted["post_time"])
        self.assertEqual(history[1]["new_post_time_ms"], unrelated["post_time"])
        self.assertIs(listener.last_callback_decision, history[-1])

        serialized = json.dumps(history, sort_keys=True)
        for raw_value in (
            "com.mcu.reolink",
            "Camera Alert",
            "An alarm from porch.",
            "porch",
            "com.example.private",
            "Private reminder",
            "Private message",
            "Private expanded message",
        ):
            self.assertNotIn(raw_value, serialized)

    async def test_recent_callback_history_is_oldest_first_and_bounded_to_eight(
        self,
    ):
        async def ingest(_event, _allow_automatic_wake=True):
            return 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        base_post_time = 1786700002000
        previous_state = None

        for index in range(8):
            attributes = {
                "package": "other.app",
                "post_time": base_post_time + index,
            }
            new_state = _state(attributes)
            listener._handle_state_change(
                _state_event(previous_state, new_state)
            )
            await hass.created_tasks[-1]
            previous_state = new_state
            self.assertIs(
                listener.last_callback_decision,
                listener.recent_callback_decisions[-1],
            )

        original_entries = list(listener.recent_callback_decisions)
        self.assertEqual(listener.recent_callback_decisions.maxlen, 8)
        self.assertEqual(len(original_entries), 8)
        self.assertEqual(
            [entry["new_post_time_ms"] for entry in original_entries],
            [base_post_time + index for index in range(8)],
        )
        self.assertTrue(
            all(entry["rejection_reason"] == "package_not_reolink" for entry in original_entries)
        )

        ninth_attributes = {
            "package": "other.app",
            "post_time": base_post_time + 8,
        }
        listener._handle_state_change(
            _state_event(previous_state, _state(ninth_attributes))
        )
        await hass.created_tasks[-1]

        final_entries = list(listener.recent_callback_decisions)
        self.assertEqual(len(final_entries), 8)
        self.assertEqual(
            [entry["new_post_time_ms"] for entry in final_entries],
            [base_post_time + index for index in range(1, 9)],
        )
        for retained, original in zip(final_entries[:-1], original_entries[1:]):
            self.assertIs(retained, original)
        self.assertNotIn(original_entries[0], final_entries)
        self.assertIs(listener.last_callback_decision, final_entries[-1])

    async def test_ingest_once_then_duplicate_rejected(self):
        queue = events.EventQueue()

        async def ingest(event, _allow_automatic_wake=True):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
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

    async def test_stale_processed_notification_repost_uses_callback_time(self):
        source = bridge.notification_event_from_attributes(
            REAL_NOTIFICATION, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None and source.notification_post_time is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(source))
        queue.remove(source.event_id)

        async def ingest(event, _allow_automatic_wake=True):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        observed_at = source.notification_post_time + timedelta(hours=26)
        self.assertTrue(
            await listener.async_process_attributes(
                REAL_NOTIFICATION,
                observed_at=observed_at,
                allow_stale_repost=True,
            )
        )
        self.assertEqual(len(queue.pending), 1)
        promoted = queue.pending[0]
        self.assertTrue(promoted.event_id.startswith("android-repost:"))
        self.assertEqual(promoted.notification_post_time, observed_at)
        self.assertEqual(promoted.alarm_time, observed_at)
        self.assertEqual(listener.stale_repost_promoted_count, 1)
        self.assertTrue(listener.last_stale_repost_promoted)
        self.assertEqual(
            listener.last_effective_event_time_source,
            "sensor_callback_repost",
        )
        self.assertFalse(listener.last_duplicate_rejected)

    async def test_stale_repost_debounce_suppresses_rapid_notification_update(self):
        source = bridge.notification_event_from_attributes(
            REAL_NOTIFICATION, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None and source.notification_post_time is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(source))
        queue.remove(source.event_id)

        calls = []

        async def ingest(event, allow_automatic_wake=True):
            calls.append((event.event_id, allow_automatic_wake))
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        observed_at = source.notification_post_time + timedelta(hours=26)
        self.assertTrue(
            await listener.async_process_attributes(
                REAL_NOTIFICATION,
                observed_at=observed_at,
                allow_stale_repost=True,
            )
        )
        self.assertFalse(
            await listener.async_process_attributes(
                REAL_NOTIFICATION,
                observed_at=observed_at + timedelta(seconds=5),
                allow_stale_repost=True,
            )
        )
        self.assertEqual(listener.stale_repost_promoted_count, 1)
        self.assertEqual(listener.stale_repost_suppressed_count, 1)
        self.assertEqual(len(queue.pending), 1)
        self.assertEqual(
            [allow_wake for _event_id, allow_wake in calls],
            [False, True, False],
        )

    async def test_direct_stale_duplicate_does_not_promote_without_provenance(self):
        source = bridge.notification_event_from_attributes(
            REAL_NOTIFICATION, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None and source.notification_post_time is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(source))
        queue.remove(source.event_id)

        calls = []

        async def ingest(event, allow_automatic_wake=True):
            calls.append((event.event_id, allow_automatic_wake))
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        self.assertFalse(
            await listener.async_process_attributes(
                REAL_NOTIFICATION,
                observed_at=source.notification_post_time + timedelta(hours=26),
            )
        )
        self.assertEqual(listener.stale_repost_promoted_count, 0)
        self.assertEqual(queue.pending, ())
        self.assertEqual(calls, [(source.event_id, False)])

    async def test_initial_old_processed_state_does_not_repost_or_wake(self):
        source = bridge.notification_event_from_attributes(
            REAL_NOTIFICATION, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(source))
        queue.remove(source.event_id)
        calls = []

        async def ingest(event, allow_automatic_wake=True):
            calls.append((event.event_id, allow_automatic_wake))
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass,
            "sensor.phone_last_notification",
            "porch",
            "camera-1",
            ingest,
            initial_event=source,
        )
        listener._handle_state_change(_state_event(None, _state(REAL_NOTIFICATION)))
        await asyncio.gather(*hass.created_tasks)
        self.assertEqual(calls, [(source.event_id, False)])
        self.assertEqual(queue.pending, ())
        self.assertEqual(listener.stale_repost_promoted_count, 0)
        self.assertFalse(listener.last_event_queued)

    async def test_initial_unseen_stale_state_is_retained_without_wake(self):
        stale_time = datetime.now(UTC) - timedelta(hours=26)
        attributes = {
            **REAL_NOTIFICATION,
            "post_time": round(stale_time.timestamp() * 1000),
        }
        queue = events.EventQueue()
        wake_permissions = []

        async def ingest(event, allow_automatic_wake=True):
            wake_permissions.append(allow_automatic_wake)
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        listener._handle_state_change(_state_event(None, _state(attributes)))
        await asyncio.gather(*hass.created_tasks)
        self.assertEqual(len(queue.pending), 1)
        self.assertEqual(wake_permissions, [False])
        self.assertEqual(listener.stale_repost_promoted_count, 0)

    async def test_initial_unseen_fresh_state_is_accepted_with_wake_permission(self):
        fresh_time = datetime.now(UTC) - timedelta(seconds=5)
        attributes = {
            **REAL_NOTIFICATION,
            "post_time": round(fresh_time.timestamp() * 1000),
        }
        queue = events.EventQueue()
        wake_permissions = []

        async def ingest(event, allow_automatic_wake=True):
            wake_permissions.append(allow_automatic_wake)
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        listener._handle_state_change(_state_event(None, _state(attributes)))
        await asyncio.gather(*hass.created_tasks)
        self.assertEqual(len(queue.pending), 1)
        self.assertEqual(wake_permissions, [True])
        self.assertEqual(listener.stale_repost_promoted_count, 0)

    async def test_runtime_same_source_update_can_promote_one_stale_repost(self):
        stale_time = datetime.now(UTC) - timedelta(hours=26)
        attributes = {
            **REAL_NOTIFICATION,
            "post_time": round(stale_time.timestamp() * 1000),
        }
        source = bridge.notification_event_from_attributes(
            attributes, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(source))
        queue.remove(source.event_id)

        calls = []

        async def ingest(event, allow_automatic_wake=True):
            calls.append((event.event_id, allow_automatic_wake))
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        listener._handle_state_change(
            _state_event(_state(attributes), _state(attributes))
        )
        await asyncio.gather(*hass.created_tasks)
        self.assertEqual(len(queue.pending), 1)
        self.assertTrue(queue.pending[0].event_id.startswith("android-repost:"))
        self.assertEqual(listener.stale_repost_promoted_count, 1)
        self.assertEqual(calls[0], (source.event_id, False))
        self.assertTrue(calls[1][0].startswith("android-repost:"))
        self.assertTrue(calls[1][1])

    async def test_runtime_unseen_stale_source_only_wakes_promoted_repost(self):
        stale_time = datetime.now(UTC) - timedelta(hours=26)
        attributes = {
            **REAL_NOTIFICATION,
            "post_time": round(stale_time.timestamp() * 1000),
        }
        source = bridge.notification_event_from_attributes(
            attributes, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None
        queue = events.EventQueue()
        calls = []

        async def ingest(event, allow_automatic_wake=True):
            calls.append((event.event_id, allow_automatic_wake))
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        listener._handle_state_change(
            _state_event(_state(attributes), _state(attributes))
        )
        await asyncio.gather(*hass.created_tasks)

        self.assertEqual(len(queue.pending), 2)
        self.assertEqual(calls[0], (source.event_id, False))
        self.assertTrue(calls[1][0].startswith("android-repost:"))
        self.assertTrue(calls[1][1])
        self.assertEqual(sum(allow_wake for _event_id, allow_wake in calls), 1)
        self.assertEqual(listener.stale_repost_promoted_count, 1)

    async def test_runtime_repost_candidate_never_creates_two_wake_attempts(self):
        observed_at = datetime.now(UTC)
        attributes = {
            **REAL_NOTIFICATION,
            "post_time": round(
                (observed_at - timedelta(seconds=400)).timestamp() * 1000
            ),
        }
        source = bridge.notification_event_from_attributes(
            attributes, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None
        queue = events.EventQueue()
        calls = []

        async def ingest(event, allow_automatic_wake=True):
            calls.append((event.event_id, allow_automatic_wake))
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        self.assertTrue(
            await listener.async_process_attributes(
                attributes,
                observed_at=observed_at,
                allow_stale_repost=True,
                allow_automatic_wake=True,
            )
        )

        self.assertEqual(len(queue.pending), 2)
        self.assertEqual(calls[0], (source.event_id, False))
        self.assertTrue(calls[1][0].startswith("android-repost:"))
        self.assertTrue(calls[1][1])
        self.assertEqual(sum(allow_wake for _event_id, allow_wake in calls), 1)

    async def test_restored_telemetry_does_not_debounce_first_runtime_repost(self):
        stale_time = datetime.now(UTC) - timedelta(hours=26)
        attributes = {
            **REAL_NOTIFICATION,
            "post_time": round(stale_time.timestamp() * 1000),
        }
        source = bridge.notification_event_from_attributes(
            attributes, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(source))

        calls = []

        async def ingest(event, allow_automatic_wake=True):
            calls.append((event.event_id, allow_automatic_wake))
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass,
            "sensor.phone_last_notification",
            "porch",
            "camera-1",
            ingest,
            initial_event=source,
        )
        listener._handle_state_change(
            _state_event(_state(attributes), _state(attributes))
        )
        await asyncio.gather(*hass.created_tasks)
        self.assertEqual(len(queue.pending), 2)
        self.assertTrue(queue.pending[-1].event_id.startswith("android-repost:"))
        self.assertEqual(listener.stale_repost_promoted_count, 1)
        self.assertEqual(
            [allow_wake for _event_id, allow_wake in calls],
            [False, True],
        )

    async def test_unrelated_runtime_update_does_not_enable_stale_promotion(self):
        source = bridge.notification_event_from_attributes(
            REAL_NOTIFICATION, expected_device_name="porch", uid="camera-1"
        )
        assert source is not None
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(source))
        queue.remove(source.event_id)

        async def ingest(event, _allow_automatic_wake=True):
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        unrelated = {
            "package": "com.example.other",
            "android.title": "Other",
            "android.text": "Not Reolink",
            "post_time": 1786700002000,
        }
        listener._handle_state_change(
            _state_event(_state(unrelated), _state(REAL_NOTIFICATION))
        )
        await asyncio.gather(*hass.created_tasks)
        self.assertEqual(queue.pending, ())
        self.assertEqual(listener.stale_repost_promoted_count, 0)

    async def test_non_reolink_notification_never_calls_ingest(self):
        calls = []

        async def ingest(event, _allow_automatic_wake=True):
            calls.append(event)
            return 1

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        attrs = {**REAL_NOTIFICATION, "package": "other.app"}
        self.assertFalse(await listener.async_process_attributes(attrs))
        self.assertEqual(calls, [])

    async def test_unrelated_notification_does_not_erase_last_reolink_telemetry(self):
        queue = events.EventQueue()

        async def ingest(event, _allow_automatic_wake=True):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        self.assertTrue(await listener.async_process_attributes(REAL_NOTIFICATION))
        saved_time = listener.last_reolink_notification_time
        self.assertFalse(
            await listener.async_process_attributes(
                {
                    "package": "com.example.other",
                    "android.title": "Other",
                    "android.text": "Not Reolink",
                    "post_time": 1786700002000,
                }
            )
        )
        self.assertTrue(listener.last_event_matched)
        self.assertTrue(listener.last_camera_mapped)
        self.assertTrue(listener.last_event_queued)
        self.assertFalse(listener.last_duplicate_rejected)
        self.assertEqual(listener.last_reolink_notification_time, saved_time)
        self.assertEqual(listener.last_reolink_notification_camera, "porch")

    async def test_beta29_tracks_sensor_updates_and_post_time_changes(self):
        queue = events.EventQueue()

        async def ingest(event, _allow_automatic_wake=True):
            return 1 if queue.enqueue(event) else 0

        listener = bridge.NotificationBridge(
            _FakeHass(), "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        self.assertTrue(await listener.async_process_attributes(REAL_NOTIFICATION))
        first_fingerprint = listener.last_event_fingerprint
        self.assertEqual(listener.sensor_state_change_count, 1)
        self.assertEqual(listener.matched_notification_update_count, 1)
        self.assertTrue(listener.last_post_time_changed)
        self.assertEqual(listener.last_notification_post_time_ms, 1786700000188)
        self.assertTrue(first_fingerprint)
        self.assertIsNotNone(listener.last_sensor_state_change_time)
        self.assertIsNotNone(listener.last_processing_lag_seconds)

        self.assertFalse(await listener.async_process_attributes(REAL_NOTIFICATION))
        self.assertEqual(listener.sensor_state_change_count, 2)
        self.assertEqual(listener.matched_notification_update_count, 2)
        self.assertFalse(listener.last_post_time_changed)
        self.assertEqual(listener.last_event_fingerprint, first_fingerprint)
        self.assertTrue(listener.last_duplicate_rejected)
        self.assertTrue(listener.last_callback_decision["normalized"])
        self.assertFalse(listener.last_callback_decision["post_time_changed"])
        self.assertTrue(listener.last_callback_decision["duplicate_rejected"])
        self.assertEqual(
            listener.last_callback_decision["queue_result"],
            "duplicate_rejected",
        )

        newer = {**REAL_NOTIFICATION, "post_time": 1786700001188}
        self.assertTrue(await listener.async_process_attributes(newer))
        self.assertEqual(listener.sensor_state_change_count, 3)
        self.assertEqual(listener.matched_notification_update_count, 3)
        self.assertTrue(listener.last_post_time_changed)
        self.assertEqual(listener.last_notification_post_time_ms, 1786700001188)
        self.assertNotEqual(listener.last_event_fingerprint, first_fingerprint)

    async def test_beta29_counts_unrelated_sensor_update_without_overwriting_match(self):
        queue = events.EventQueue()

        async def ingest(event, _allow_automatic_wake=True):
            return 1 if queue.enqueue(event) else 0

        hass = _AsyncFakeHass()
        listener = bridge.NotificationBridge(
            hass, "sensor.phone_last_notification", "porch", "camera-1", ingest
        )
        self.assertTrue(await listener.async_process_attributes(REAL_NOTIFICATION))
        saved_post_time = listener.last_notification_post_time_ms
        legacy_fields = (
            listener.last_event_matched,
            listener.last_camera_mapped,
            listener.last_event_queued,
            listener.last_duplicate_rejected,
            listener.last_notification_post_time_ms,
            listener.last_post_time_changed,
            listener.last_event_fingerprint,
        )
        unrelated = {
            "package": "com.example.other",
            "android.title": "Other",
            "android.text": "Not Reolink",
            "post_time": 1786700002000,
        }
        listener._handle_state_change(
            _state_event(_state(REAL_NOTIFICATION), _state(unrelated))
        )
        self.assertFalse(await hass.created_tasks[-1])
        self.assertEqual(listener.sensor_state_change_count, 2)
        self.assertEqual(listener.matched_notification_update_count, 1)
        self.assertEqual(listener.last_notification_post_time_ms, saved_post_time)
        self.assertEqual(
            (
                listener.last_event_matched,
                listener.last_camera_mapped,
                listener.last_event_queued,
                listener.last_duplicate_rejected,
                listener.last_notification_post_time_ms,
                listener.last_post_time_changed,
                listener.last_event_fingerprint,
            ),
            legacy_fields,
        )
        decision = listener.last_callback_decision
        self.assertEqual(decision["change_classification"], "attributes_only")
        self.assertFalse(decision["normalized"])
        self.assertEqual(decision["rejection_reason"], "package_not_reolink")
        self.assertEqual(decision["new_post_time_ms"], 1786700002000)
        self.assertTrue(decision["post_time_parse_valid"])
        self.assertFalse(decision["stale_repost_evaluation_reached"])
        self.assertIsNone(decision["duplicate_rejected"])
        self.assertEqual(decision["queue_result"], "not_reached")

        serialized = json.dumps(decision, sort_keys=True)
        for raw_value in (
            "com.example.other",
            "Other",
            "Not Reolink",
            "porch",
        ):
            self.assertNotIn(raw_value, serialized)
        diagnostics_source = (COMPONENT / "diagnostics.py").read_text()
        self.assertIn('"last_callback_decision"', diagnostics_source)
        self.assertIn('"recent_callback_decisions"', diagnostics_source)
        self.assertIn('"recent_callback_decisions_limit"', diagnostics_source)
        self.assertIn('"recent_callback_decisions_order"', diagnostics_source)
        self.assertIn('"notification_identity_source_configured"', diagnostics_source)
        self.assertIn('"notification_identity_listener_active"', diagnostics_source)
        self.assertIn(
            '"recent_notification_identity_observations"', diagnostics_source
        )
        self.assertIn(
            '"recent_notification_identity_observations_limit"', diagnostics_source
        )
        self.assertIn(
            '"recent_notification_identity_observations_order"', diagnostics_source
        )

    async def test_pending_android_event_restores_runtime_telemetry_after_reload(self):
        event = bridge.notification_event_from_attributes(
            REAL_NOTIFICATION, expected_device_name="porch", uid="camera-1"
        )
        assert event is not None

        async def ingest(_event, _allow_automatic_wake=True):
            return 0

        listener = bridge.NotificationBridge(
            _FakeHass(),
            "sensor.phone_last_notification",
            "porch",
            "camera-1",
            ingest,
            initial_event=event,
        )
        self.assertTrue(listener.last_event_matched)
        self.assertTrue(listener.last_camera_mapped)
        self.assertTrue(listener.last_event_queued)
        self.assertFalse(listener.last_duplicate_rejected)
        self.assertEqual(listener.last_reolink_notification_time, event.notification_post_time)
        self.assertEqual(listener.last_reolink_notification_camera, "porch")


if __name__ == "__main__":
    unittest.main()
