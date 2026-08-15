"""Offline tests for Milestone 3B.2a recording candidate selection."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_recording_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_probe")


class FakeVod:
    def __init__(self, name: str, start: datetime, end: datetime, size: int = 100):
        self.file_name = name
        self.start_time = start
        self.end_time = end
        self.size = size


class CandidateSelectionTests(unittest.TestCase):
    def test_timestamp_inside_recording_wins(self):
        target = datetime(2026, 8, 15, 10, 22, 49)
        files = [
            FakeVod("old.mp4", datetime(2026, 8, 15, 10, 20), datetime(2026, 8, 15, 10, 20, 29)),
            FakeVod("match.mp4", datetime(2026, 8, 15, 10, 22, 37), datetime(2026, 8, 15, 10, 23, 6), 10521742),
        ]
        result = probe.select_recording_candidate(target, files)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.file_name, "match.mp4")
        self.assertEqual(result.distance_seconds, 0)
        self.assertEqual(result.size, 10521742)

    def test_nearest_interval_within_tolerance_is_accepted(self):
        target = datetime(2026, 8, 15, 10, 22, 49)
        files = [FakeVod("near.mp4", datetime(2026, 8, 15, 10, 23, 5), datetime(2026, 8, 15, 10, 23, 34))]
        result = probe.select_recording_candidate(target, files)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.distance_seconds, 16)

    def test_outside_tolerance_is_rejected(self):
        target = datetime(2026, 8, 15, 10, 22, 49)
        files = [FakeVod("far.mp4", datetime(2026, 8, 15, 10, 25), datetime(2026, 8, 15, 10, 25, 29))]
        result = probe.select_recording_candidate(
            target, files, tolerance=timedelta(seconds=30)
        )
        self.assertIsNone(result)

    def test_equal_distance_ambiguous_files_are_rejected(self):
        target = datetime(2026, 8, 15, 10, 22, 49)
        files = [
            FakeVod("before.mp4", datetime(2026, 8, 15, 10, 22, 10), datetime(2026, 8, 15, 10, 22, 39)),
            FakeVod("after.mp4", datetime(2026, 8, 15, 10, 22, 59), datetime(2026, 8, 15, 10, 23, 28)),
        ]
        self.assertIsNone(probe.select_recording_candidate(target, files))

    def test_duplicate_vod_entries_do_not_create_ambiguity(self):
        target = datetime(2026, 8, 15, 10, 22, 49)
        item = FakeVod("same.mp4", datetime(2026, 8, 15, 10, 22, 37), datetime(2026, 8, 15, 10, 23, 6))
        result = probe.select_recording_candidate(target, [item, item])
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
