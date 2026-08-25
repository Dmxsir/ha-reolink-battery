from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker_v138.py"
SENSOR = ROOT / "custom_components" / "reolink_battery" / "sensor.py"
BUTTON = ROOT / "custom_components" / "reolink_battery" / "button.py"
HE = ROOT / "custom_components" / "reolink_battery" / "translations" / "he.json"


class ManualRecoveryControlTests(unittest.TestCase):
    def test_worker_has_explicit_all_pending_recovery(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn("async def async_request_manual_recovery", source)
        self.assertIn("await coordinator.async_rearm_event(event.event_id)", source)
        self.assertIn("self._manual_recovery_event_ids.update(event_ids)", source)
        self.assertIn("manual_recovery_policy", source)
        self.assertIn("explicit_button_all_pending", source)

    def test_manual_events_bypass_freshness_but_automatic_events_do_not(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        worker = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RecordingWorker"
        )
        next_event = next(
            node for node in worker.body
            if isinstance(node, ast.FunctionDef) and node.name == "_next_android_event"
        )
        segment = ast.get_source_segment(source, next_event) or ""
        self.assertIn("event.event_id in self._manual_recovery_event_ids", segment)
        self.assertIn("self._is_fresh(event, now)", segment)

        defer_stale = next(
            node for node in worker.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_defer_stale_activated_events"
        )
        segment = ast.get_source_segment(source, defer_stale) or ""
        self.assertIn(
            "event.event_id not in self._manual_recovery_event_ids", segment
        )

    def test_completed_or_failed_manual_work_leaves_manual_set(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count("self._manual_recovery_event_ids.discard(event.event_id)"),
            2,
        )

    def test_queue_sensor_counts_android_events_only(self) -> None:
        source = SENSOR.read_text(encoding="utf-8")
        self.assertIn("class ReolinkRecordingQueueSensor", source)
        self.assertIn('translation_key="recordings_queued"', source)
        self.assertIn('event.source == "android_notification"', source)
        self.assertIn('"deferred"', source)
        self.assertIn('"fresh_automatic"', source)
        self.assertIn('"stale"', source)

    def test_recovery_button_calls_worker_api(self) -> None:
        source = BUTTON.read_text(encoding="utf-8")
        self.assertIn("class ReolinkRecoverPendingRecordingsButton", source)
        self.assertIn('translation_key="recover_pending_recordings"', source)
        self.assertIn("await worker.async_request_manual_recovery()", source)

    def test_hebrew_entity_names_are_present(self) -> None:
        source = HE.read_text(encoding="utf-8")
        self.assertIn("סרטונים בתור", source)
        self.assertIn("הורד סרטונים חסרים", source)


if __name__ == "__main__":
    unittest.main()
