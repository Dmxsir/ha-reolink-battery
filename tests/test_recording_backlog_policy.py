"""Battery-safe automatic recording backlog policy tests; no camera is contacted."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_backlog_test"

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
events = load_module("events")
worker_module = load_module("recording_worker")


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
        self.saved = []

    @property
    def pending_events(self):
        return self.queue.pending

    @property
    def deferred_event_count(self):
        return self.queue.deferred_count

    @property
    def deferred_event_ids(self):
        return self.queue.deferred_event_ids

    @property
    def completed_recording_fingerprints(self):
        return self.queue.completed_recording_fingerprints

    @property
    def last_deferred_event(self):
        return self.queue.last_deferred

    def is_event_deferred(self, event_id):
        return event_id in self.queue.deferred_event_ids

    async def async_defer_event(self, event_id, reason, *, deferred_at=None):
        changed = self.queue.defer(
            event_id, reason, deferred_at or datetime.now(UTC)
        )
        if changed:
            self.saved.append(self.queue.as_storage())
        return changed

    async def async_complete_event(self, event_id, *, completed_recording=None):
        changed = self.queue.remove(event_id)
        if completed_recording is not None:
            changed = self.queue.remember_completed_recording(completed_recording) or changed
        if changed:
            self.saved.append(self.queue.as_storage())
        return changed


def make_worker(queue=None):
    coordinator = FakeCoordinator(queue)
    runtime = types.SimpleNamespace(coordinator=coordinator)
    entry = types.SimpleNamespace(runtime_data=runtime, entry_id="test-entry", data={})
    hass = types.SimpleNamespace()
    return worker_module.RecordingWorker(hass, entry), coordinator


class EventQueuePersistenceTests(unittest.TestCase):
    def test_setup_uses_event_specific_and_bounded_startup_activation(self):
        source = (COMPONENT / "__init__.py").read_text()
        self.assertIn("recording_worker.notify(event.event_id)", source)
        self.assertIn("await runtime.recording_worker.async_prepare_startup_recovery()", source)
        self.assertNotIn("for event in coordinator.pending_events\n        ):\n            runtime.recording_worker.notify()", source)

    def test_deferred_state_survives_serialization_and_v1_without_field_loads(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        queue = events.EventQueue()
        event = make_event("event-1", now)
        self.assertTrue(queue.enqueue(event))
        self.assertTrue(queue.defer(event.event_id, "automatic_retries_exhausted", now))

        restored = events.EventQueue()
        restored.load(queue.as_storage())
        self.assertEqual(restored.pending, (event,))
        self.assertEqual(restored.deferred_event_ids, frozenset({event.event_id}))
        self.assertEqual(restored.last_deferred.reason, "automatic_retries_exhausted")

        legacy = events.EventQueue()
        legacy.load(
            {
                "processed": [event.event_id],
                "pending": [event.as_storage()],
                "completed_recordings": [],
            }
        )
        self.assertEqual(legacy.pending, (event,))
        self.assertEqual(legacy.deferred_event_ids, frozenset())

    def test_completed_recording_fingerprint_dedupe_round_trips(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        recording = events.CompletedRecording("fingerprint", now, now, 1234)
        queue = events.EventQueue()
        self.assertTrue(queue.remember_completed_recording(recording))

        restored = events.EventQueue()
        restored.load(queue.as_storage())
        self.assertEqual(
            restored.completed_recording_fingerprints,
            frozenset({"fingerprint"}),
        )
        self.assertFalse(restored.remember_completed_recording(recording))

    def test_remove_and_pending_eviction_prune_deferred_metadata(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        queue = events.EventQueue()
        completed = make_event("completed", now)
        queue.enqueue(completed)
        queue.defer(completed.event_id, "automatic_retries_exhausted", now)
        self.assertTrue(queue.remove(completed.event_id))
        self.assertNotIn(completed.event_id, queue.deferred_event_ids)

        evicted = make_event("evicted", now)
        queue.enqueue(evicted)
        queue.defer(evicted.event_id, "automatic_retries_exhausted", now)
        for index in range(const.MAX_PENDING_EVENTS):
            queue.enqueue(make_event(f"new-{index}", now + timedelta(seconds=index + 1)))
        self.assertNotIn(evicted, queue.pending)
        self.assertNotIn(evicted.event_id, queue.deferred_event_ids)

    def test_load_prunes_orphan_and_malformed_deferred_metadata(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        pending = make_event("pending", now)
        recording = events.CompletedRecording("fingerprint", now, now, 1234)
        restored = events.EventQueue()
        restored.load(
            {
                "processed": [pending.event_id],
                "pending": [pending.as_storage()],
                "completed_recordings": [recording.as_storage()],
                "deferred_events": [
                    {
                        "event_id": pending.event_id,
                        "reason": "automatic_retries_exhausted",
                        "deferred_at": now.isoformat(),
                    },
                    {
                        "event_id": "orphan",
                        "reason": "orphan",
                        "deferred_at": now.isoformat(),
                    },
                    {"event_id": pending.event_id, "deferred_at": "invalid"},
                    {"event_id": pending.event_id, "deferred_at": 10**1000},
                    {},
                    "not-a-record",
                ],
            }
        )
        pending_ids = {event.event_id for event in restored.pending}
        self.assertEqual(restored.pending, (pending,))
        self.assertEqual(restored.completed_recording_count, 1)
        self.assertEqual(restored.deferred_event_ids, frozenset({pending.event_id}))
        self.assertLessEqual(restored.deferred_event_ids, pending_ids)


class RecordingBacklogPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_completion_prunes_deferred_event_metadata(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        queue = events.EventQueue()
        event = make_event("completed-deferred", now)
        recording = events.CompletedRecording("completed-fingerprint", now, now, 1234)
        queue.enqueue(event)
        queue.defer(event.event_id, "automatic_retries_exhausted", now)
        _worker, coordinator = make_worker(queue)

        self.assertTrue(
            await coordinator.async_complete_event(
                event.event_id,
                completed_recording=recording,
            )
        )
        self.assertEqual(coordinator.pending_events, ())
        self.assertNotIn(event.event_id, coordinator.deferred_event_ids)
        self.assertIn(
            recording.fingerprint,
            coordinator.completed_recording_fingerprints,
        )

    async def test_failed_event_is_persistently_deferred_after_bounded_retries(self):
        now = datetime.now(UTC)
        queue = events.EventQueue()
        event = make_event("failed", now)
        queue.enqueue(event)
        worker, coordinator = make_worker(queue)
        worker.notify(event.event_id)
        worker._settle_event = AsyncMock(return_value=True)
        worker._process_once = AsyncMock(return_value=False)
        original_delays = worker_module.RETRY_DELAYS_SECONDS
        worker_module.RETRY_DELAYS_SECONDS = (0.0, 0.0)
        try:
            await worker._process_trigger()
        finally:
            worker_module.RETRY_DELAYS_SECONDS = original_delays

        self.assertEqual(worker._process_once.await_count, 3)
        self.assertIn(event.event_id, coordinator.deferred_event_ids)
        self.assertEqual(
            coordinator.queue.last_deferred.reason,
            "automatic_retries_exhausted",
        )
        self.assertEqual(worker.state.deferred_count, 1)

    async def test_new_notification_does_not_clear_previous_deferral(self):
        now = datetime.now(UTC)
        queue = events.EventQueue()
        old = make_event("old", now - timedelta(minutes=2))
        new = make_event("new", now)
        queue.enqueue(old)
        queue.enqueue(new)
        queue.defer(old.event_id, "automatic_retries_exhausted", now)
        worker, coordinator = make_worker(queue)

        worker.notify(new.event_id)

        self.assertIn(old.event_id, coordinator.deferred_event_ids)
        self.assertEqual(worker._next_android_event(now), new)
        self.assertEqual(worker.state.deferred_rearmed_count, 0)

    async def test_fresh_c_runs_without_rearming_deferred_a_or_b(self):
        now = datetime.now(UTC)
        queue = events.EventQueue()
        event_a = make_event("A", now - timedelta(minutes=2))
        event_b = make_event("B", now - timedelta(minutes=1))
        event_c = make_event("C", now)
        for event in (event_a, event_b, event_c):
            queue.enqueue(event)
        queue.defer(event_a.event_id, "failure-a", now - timedelta(hours=2))
        queue.defer(event_b.event_id, "failure-b", now - timedelta(hours=1))
        deferred_before = {
            item["event_id"]: item
            for item in queue.as_storage()["deferred_events"]
        }
        worker, coordinator = make_worker(queue)
        worker.notify(event_c.event_id)
        worker._settle_event = AsyncMock(return_value=True)
        attempted = []

        async def complete_c(event):
            attempted.append(event.event_id)
            await coordinator.async_complete_event(event.event_id)
            return True

        worker._process_once = AsyncMock(side_effect=complete_c)
        await worker._process_trigger()

        self.assertEqual(attempted, [event_c.event_id])
        self.assertEqual(
            coordinator.deferred_event_ids,
            frozenset({event_a.event_id, event_b.event_id}),
        )
        deferred_after = {
            item["event_id"]: item
            for item in queue.as_storage()["deferred_events"]
        }
        self.assertEqual(deferred_after, deferred_before)
        restored = events.EventQueue()
        restored.load(queue.as_storage())
        self.assertEqual(restored.deferred_event_ids, coordinator.deferred_event_ids)
        self.assertEqual(worker._process_once.await_count, 1)

    async def test_startup_skips_stale_deferred_event_without_rearming(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        queue = events.EventQueue()
        stale = make_event("stale-deferred", now - timedelta(hours=1))
        queue.enqueue(stale)
        queue.defer(stale.event_id, "automatic_retries_exhausted", now)
        restored = events.EventQueue()
        restored.load(queue.as_storage())
        worker, coordinator = make_worker(restored)

        candidate = await worker.async_prepare_startup_recovery(now=now)

        self.assertIsNone(candidate)
        self.assertIn(stale.event_id, coordinator.deferred_event_ids)
        self.assertEqual(worker._activated_event_ids, set())
        self.assertFalse(worker.state.pending_trigger)

    async def test_startup_marks_stale_pending_event_deferred_without_trigger(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        queue = events.EventQueue()
        stale = make_event("stale", now - timedelta(minutes=11))
        queue.enqueue(stale)
        worker, coordinator = make_worker(queue)

        candidate = await worker.async_prepare_startup_recovery(now=now)

        self.assertIsNone(candidate)
        self.assertIn(stale.event_id, coordinator.deferred_event_ids)
        self.assertEqual(coordinator.queue.last_deferred.reason, "startup_event_stale")
        self.assertEqual(worker.state.startup_skipped_stale_count, 1)
        self.assertEqual(worker.state.stale_pending_count, 1)
        self.assertFalse(worker.state.pending_trigger)
        policy = worker.policy_diagnostics()
        self.assertEqual(policy["persistent_deferred_count"], 1)
        self.assertEqual(policy["eligible_fresh_pending_count"], 0)
        self.assertEqual(policy["stale_pending_count"], 1)
        self.assertEqual(policy["last_deferred_reason"], "startup_event_stale")
        self.assertEqual(policy["automatic_event_max_age_seconds"], 600)

    async def test_startup_activates_only_newest_fresh_pending_event(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        queue = events.EventQueue()
        older = make_event("older", now - timedelta(minutes=4))
        newest = make_event("newest", now - timedelta(minutes=1))
        queue.enqueue(older)
        queue.enqueue(newest)
        worker, coordinator = make_worker(queue)

        candidate = await worker.async_prepare_startup_recovery(now=now)

        self.assertEqual(candidate, newest)
        self.assertEqual(worker._activated_event_ids, {newest.event_id})
        self.assertEqual(
            coordinator.deferred_event_ids,
            frozenset({older.event_id}),
        )
        self.assertEqual(coordinator.queue.last_deferred.reason, "startup_not_selected")
        self.assertEqual(worker.state.startup_not_selected_count, 1)
        self.assertEqual(worker.state.eligible_fresh_pending_count, 1)
        self.assertTrue(worker.state.startup_recovery_eligible)
        self.assertTrue(worker.state.pending_trigger)

    async def test_repeated_startup_does_not_drain_unselected_fresh_backlog(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        older = make_event("older", now - timedelta(minutes=4))
        newest = make_event("newest", now - timedelta(minutes=1))
        queue = events.EventQueue()
        queue.enqueue(older)
        queue.enqueue(newest)
        worker, coordinator = make_worker(queue)

        candidate = await worker.async_prepare_startup_recovery(now=now)

        self.assertEqual(candidate, newest)
        self.assertIn(older.event_id, coordinator.deferred_event_ids)
        self.assertEqual(coordinator.queue.last_deferred.reason, "startup_not_selected")
        await coordinator.async_complete_event(newest.event_id)

        restored = events.EventQueue()
        restored.load(queue.as_storage())
        restarted_worker, restarted_coordinator = make_worker(restored)
        restarted_candidate = await restarted_worker.async_prepare_startup_recovery(
            now=now + timedelta(minutes=1)
        )

        self.assertIsNone(restarted_candidate)
        self.assertEqual(restored.pending, (older,))
        self.assertIn(older.event_id, restarted_coordinator.deferred_event_ids)
        self.assertEqual(restarted_worker._activated_event_ids, set())
        self.assertFalse(restarted_worker.state.pending_trigger)

    async def test_startup_freshness_boundary_is_inclusive_at_600_seconds(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        below = make_event("below", now - timedelta(seconds=599, milliseconds=999))
        boundary = make_event("boundary", now - timedelta(seconds=600))
        above = make_event("above", now - timedelta(seconds=600, milliseconds=1))
        queue = events.EventQueue()
        for event in (below, boundary, above):
            queue.enqueue(event)
        worker, coordinator = make_worker(queue)

        candidate = await worker.async_prepare_startup_recovery(now=now)

        self.assertTrue(events.is_automatic_event_fresh(below, now))
        self.assertTrue(events.is_automatic_event_fresh(boundary, now))
        self.assertFalse(events.is_automatic_event_fresh(above, now))
        self.assertEqual(candidate, below)
        self.assertIn(boundary.event_id, coordinator.deferred_event_ids)
        self.assertIn(above.event_id, coordinator.deferred_event_ids)
        reasons = {
            item["event_id"]: item["reason"]
            for item in coordinator.queue.as_storage()["deferred_events"]
        }
        self.assertEqual(reasons[boundary.event_id], "startup_not_selected")
        self.assertEqual(reasons[above.event_id], "startup_event_stale")
        self.assertEqual(worker.state.eligible_fresh_pending_count, 1)
        self.assertEqual(worker.state.stale_pending_count, 1)
        self.assertEqual(worker.state.startup_not_selected_count, 1)
        self.assertEqual(worker.state.deferred_count, 2)

    async def test_newest_activated_fresh_event_wins(self):
        now = datetime.now(UTC)
        queue = events.EventQueue()
        older = make_event("older", now - timedelta(minutes=2))
        newest = make_event("newest", now)
        queue.enqueue(older)
        queue.enqueue(newest)
        worker, _coordinator = make_worker(queue)
        worker.notify(older.event_id)
        worker.notify(newest.event_id)

        self.assertEqual(worker._next_android_event(now), newest)

    async def test_retry_wait_is_preempted_by_genuinely_newer_fresh_event(self):
        now = datetime.now(UTC)
        queue = events.EventQueue()
        older = make_event("older", now - timedelta(minutes=2))
        newest = make_event("newest", now)
        queue.enqueue(older)
        worker, _coordinator = make_worker(queue)
        worker.notify(older.event_id)
        worker._trigger.clear()
        worker.state.pending_trigger = False

        retry_wait = asyncio.create_task(
            worker._wait_retry_or_newer_event(older, 30.0)
        )
        await asyncio.sleep(0)
        queue.enqueue(newest)
        worker.notify(newest.event_id)

        self.assertFalse(await retry_wait)
        self.assertEqual(worker.state.retry_preemptions, 1)
        self.assertEqual(worker.state.last_preempting_event_time, newest.alarm_time)

    async def test_successful_processing_removes_pending_event_once(self):
        now = datetime.now(UTC)
        queue = events.EventQueue()
        event = make_event("success", now)
        queue.enqueue(event)
        worker, coordinator = make_worker(queue)
        worker.notify(event.event_id)
        worker._settle_event = AsyncMock(return_value=True)
        ready_events = []

        async def complete_once(processed):
            changed = await coordinator.async_complete_event(processed.event_id)
            if changed:
                ready_events.append(processed.event_id)
            return True

        worker._process_once = AsyncMock(side_effect=complete_once)
        await worker._process_trigger()
        await worker._process_trigger()

        self.assertEqual(coordinator.pending_events, ())
        self.assertEqual(ready_events, [event.event_id])
        self.assertEqual(worker._process_once.await_count, 1)


if __name__ == "__main__":
    unittest.main()
