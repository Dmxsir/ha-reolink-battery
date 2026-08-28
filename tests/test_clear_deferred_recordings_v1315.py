from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_clear_deferred_test"

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


load_module("const")
events = load_module("events")

BUTTON = COMPONENT / "button.py"
COORDINATOR = COMPONENT / "coordinator.py"
WORKER = COMPONENT / "recording_worker_v138.py"
STRINGS = COMPONENT / "strings.json"
HE = COMPONENT / "translations" / "he.json"


def make_event(event_id: str, source: str = "android_notification"):
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    return events.CloudEvent(
        event_id=event_id,
        uid="camera-test",
        alarm_time=now,
        alarm_type="MOTION",
        source=source,
        device_name="test-camera",
        notification_post_time=now,
    )


class DeferredQueueCleanupTests(unittest.TestCase):
    def test_discard_deferred_removes_only_selected_deferred_pending(self) -> None:
        queue = events.EventQueue()
        dead_a = make_event("dead-a")
        dead_b = make_event("dead-b")
        keep_fresh = make_event("keep-fresh")
        keep_cloud = make_event("keep-cloud", source="message_center")
        for event in (dead_a, dead_b, keep_fresh, keep_cloud):
            self.assertTrue(queue.enqueue(event))

        now = datetime(2026, 8, 28, 12, 5, tzinfo=UTC)
        self.assertTrue(queue.defer(dead_a.event_id, "dead-a", now))
        self.assertTrue(queue.defer(dead_b.event_id, "dead-b", now))
        self.assertTrue(queue.defer(keep_cloud.event_id, "cloud-deferred", now))
        processed_before = queue.processed_count

        removed = queue.discard_deferred({dead_a.event_id, dead_b.event_id})

        self.assertEqual(set(removed), {dead_a.event_id, dead_b.event_id})
        self.assertEqual(
            {event.event_id for event in queue.pending},
            {keep_fresh.event_id, keep_cloud.event_id},
        )
        self.assertEqual(queue.deferred_event_ids, frozenset({keep_cloud.event_id}))
        self.assertEqual(queue.processed_count, processed_before)

    def test_cleared_event_stays_processed_after_storage_round_trip(self) -> None:
        queue = events.EventQueue()
        dead = make_event("dead")
        queue.enqueue(dead)
        queue.defer(dead.event_id, "historical-miss", datetime.now(UTC))
        self.assertEqual(queue.discard_deferred({dead.event_id}), (dead.event_id,))

        restored = events.EventQueue()
        restored.load(queue.as_storage())

        self.assertEqual(restored.pending, ())
        self.assertEqual(restored.deferred_event_ids, frozenset())
        self.assertFalse(restored.enqueue(dead))

    def test_unselected_deferred_event_is_not_removed(self) -> None:
        queue = events.EventQueue()
        first = make_event("first")
        second = make_event("second")
        queue.enqueue(first)
        queue.enqueue(second)
        now = datetime.now(UTC)
        queue.defer(first.event_id, "first", now)
        queue.defer(second.event_id, "second", now)

        self.assertEqual(queue.discard_deferred({first.event_id}), (first.event_id,))
        self.assertEqual(queue.pending, (second,))
        self.assertEqual(queue.deferred_event_ids, frozenset({second.event_id}))


class DeferredCleanupControlTests(unittest.TestCase):
    def test_coordinator_persists_bulk_deferred_cleanup(self) -> None:
        source = COORDINATOR.read_text(encoding="utf-8")
        self.assertIn("async def async_clear_deferred_events", source)
        self.assertIn("self._queue.discard_deferred(candidates)", source)
        self.assertIn("await self._store.async_save(self._queue.as_storage())", source)
        self.assertIn("source is None or event.source == source", source)

    def test_worker_clears_only_android_deferred_queue_and_runtime_sets(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn("async def async_clear_deferred_recordings", source)
        self.assertIn('source="android_notification"', source)
        self.assertIn("self._activated_event_ids.discard(event_id)", source)
        self.assertIn("self._manual_recovery_event_ids.discard(event_id)", source)
        self.assertIn("self._automatic_first_attempt_credit_event_ids.discard(event_id)", source)
        self.assertIn("deferred_clear_requests", source)
        self.assertIn("deferred_clear_last_count", source)
        self.assertIn("deferred_cleared_total", source)
        self.assertIn("explicit_button_deferred_android_only", source)

    def test_clear_button_exists_and_calls_queue_only_worker_api(self) -> None:
        source = BUTTON.read_text(encoding="utf-8")
        self.assertIn("class ReolinkClearDeferredRecordingsButton", source)
        self.assertIn('translation_key="clear_deferred_recordings"', source)
        self.assertIn("await worker.async_clear_deferred_recordings()", source)
        self.assertIn("runtime.coordinator.deferred_event_ids", source)

    def test_user_facing_names_exist(self) -> None:
        self.assertIn("Clear deferred recordings", STRINGS.read_text(encoding="utf-8"))
        self.assertIn("נקה סרטונים שנדחו", HE.read_text(encoding="utf-8"))

    def test_cleanup_layer_does_not_reimplement_transport(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertNotIn("_build_full_high_cmd8", source)
        self.assertNotIn("_build_cmd13_wire", source)
        self.assertNotIn("send_periodic_ack", source)


if __name__ == "__main__":
    unittest.main()
