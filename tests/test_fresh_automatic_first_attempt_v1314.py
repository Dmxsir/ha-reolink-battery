"""v1.3.14 fresh automatic-event starvation regression tests."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_v1314_test"

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
load_module("recording_worker")
worker_module = load_module("recording_worker_v138")


def make_event(event_id: str, event_time: datetime):
    return events.CloudEvent(
        event_id=event_id,
        uid="camera-test",
        alarm_time=event_time,
        alarm_type="MOTION",
        source="android_notification",
        device_name="test-camera",
        notification_post_time=event_time,
    )


class FakeCoordinator:
    def __init__(self, queue=None):
        self.queue = queue or events.EventQueue()

    @property
    def pending_events(self):
        return self.queue.pending

    @property
    def deferred_event_count(self):
        return self.queue.deferred_count

    @property
    def completed_recording_fingerprints(self):
        return self.queue.completed_recording_fingerprints

    @property
    def last_deferred_event(self):
        return self.queue.last_deferred

    def is_event_deferred(self, event_id):
        return event_id in self.queue.deferred_event_ids

    async def async_defer_event(self, event_id, reason, *, deferred_at=None):
        return self.queue.defer(
            event_id,
            reason,
            deferred_at or datetime.now(UTC),
        )


def make_worker(queue=None):
    coordinator = FakeCoordinator(queue)
    runtime = types.SimpleNamespace(coordinator=coordinator)
    entry = types.SimpleNamespace(runtime_data=runtime, entry_id="test-entry", data={})
    hass = types.SimpleNamespace()
    return worker_module.RecordingWorker(hass, entry), coordinator


class FreshAutomaticFirstAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_notification_keeps_first_attempt_credit_after_ten_minutes(self):
        accepted_at = datetime.now(UTC)
        future = accepted_at + timedelta(minutes=11)
        event = make_event("fresh-while-worker-busy", accepted_at)
        queue = events.EventQueue()
        queue.enqueue(event)
        worker, coordinator = make_worker(queue)

        worker.notify(event.event_id)

        self.assertIn(
            event.event_id,
            worker._automatic_first_attempt_credit_event_ids,
        )
        await worker._defer_stale_activated_events(future)
        self.assertFalse(coordinator.is_event_deferred(event.event_id))
        self.assertEqual(worker._next_android_event(future), event)

        self.assertTrue(
            worker._consume_automatic_first_attempt_credit(event, now=future)
        )
        policy = worker.policy_diagnostics()
        self.assertEqual(policy["automatic_first_attempt_credit_pending"], 0)
        self.assertEqual(policy["automatic_first_attempt_credits_granted"], 1)
        self.assertEqual(policy["automatic_late_first_attempts"], 1)
        self.assertEqual(
            policy["automatic_first_attempt_credit_policy"],
            "fresh_at_activation_until_first_attempt",
        )

        await worker._defer_stale_activated_events(future)
        self.assertTrue(coordinator.is_event_deferred(event.event_id))
        self.assertEqual(
            coordinator.last_deferred_event.reason,
            "automatic_event_stale",
        )

    async def test_stale_backlog_never_receives_automatic_first_attempt_credit(self):
        now = datetime.now(UTC)
        event = make_event("already-stale", now - timedelta(minutes=11))
        queue = events.EventQueue()
        queue.enqueue(event)
        worker, coordinator = make_worker(queue)

        worker.notify(event.event_id)

        self.assertNotIn(
            event.event_id,
            worker._automatic_first_attempt_credit_event_ids,
        )
        await worker._defer_stale_activated_events(now)
        self.assertTrue(coordinator.is_event_deferred(event.event_id))

    async def test_new_fresh_motion_beats_old_manual_work_even_if_it_ages_while_busy(self):
        accepted_at = datetime.now(UTC)
        future = accepted_at + timedelta(minutes=27)
        old = make_event("old-manual", accepted_at - timedelta(hours=2))
        new = make_event("new-automatic", accepted_at)
        queue = events.EventQueue()
        queue.enqueue(old)
        queue.enqueue(new)
        worker, coordinator = make_worker(queue)

        worker._manual_recovery_event_ids.add(old.event_id)
        worker._activated_event_ids.add(old.event_id)
        worker.notify(new.event_id)

        await worker._defer_stale_activated_events(future)

        self.assertFalse(coordinator.is_event_deferred(new.event_id))
        self.assertEqual(worker._next_android_event(future), new)


if __name__ == "__main__":
    unittest.main()
