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
    def __init__(
        self,
        name: str,
        start: datetime,
        end: datetime,
        size: int = 100,
        *,
        record_id: str = "",
        xml_file_name: str = "",
        display_name: str = "",
        channel_id: int | None = None,
        stream_type: str = "",
        file_type: str = "",
        record_type: str = "",
    ):
        self.file_name = name
        self.start_time = start
        self.end_time = end
        self.size = size
        self.record_id = record_id
        self.xml_file_name = xml_file_name
        self.display_name = display_name
        self.channel_id = channel_id
        self.stream_type = stream_type
        self.file_type = file_type
        self.record_type = record_type


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

    def test_candidate_preserves_distinct_download_identity(self):
        target = datetime(2026, 8, 15, 10, 22, 49)
        item = FakeVod(
            "record-identity",
            datetime(2026, 8, 15, 10, 22, 54),
            datetime(2026, 8, 15, 10, 23, 28),
            record_id="record-identity",
            xml_file_name="/mnt/sd/actual-file.mp4",
            display_name="actual-file.mp4",
            channel_id=0,
            stream_type="mainStream",
            file_type="mp4",
            record_type="md",
        )
        result = probe.select_recording_candidate(target, [item])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.record_id, "record-identity")
        self.assertEqual(result.xml_file_name, "/mnt/sd/actual-file.mp4")
        self.assertEqual(result.display_name, "actual-file.mp4")
        self.assertEqual(result.channel_id, 0)
        self.assertEqual(result.stream_type, "mainStream")
        self.assertEqual(result.file_type, "mp4")
        self.assertEqual(result.record_type, "md")


class FileInfoParsingTests(unittest.TestCase):
    def test_file_info_keeps_id_filename_and_name_separate(self):
        xml = """<?xml version="1.0"?>
<body><FileInfoList><FileInfo>
<Id>record-identity</Id>
<fileName>/mnt/sd/actual-file.mp4</fileName>
<name>actual-file.mp4</name>
<channelId>0</channelId>
<streamType>mainStream</streamType>
<fileType>mp4</fileType>
<recordType>md</recordType>
<startTime><year>2026</year><month>8</month><day>15</day><hour>10</hour><minute>22</minute><second>54</second></startTime>
<endTime><year>2026</year><month>8</month><day>15</day><hour>10</hour><minute>23</minute><second>28</second></endTime>
<size>123456</size>
</FileInfo></FileInfoList></body>"""
        files, finished = probe._parse_file_info_page(xml)
        self.assertIsNone(finished)
        self.assertEqual(len(files), 1)
        item = files[0]
        self.assertEqual(item.file_name, "record-identity")
        self.assertEqual(item.record_id, "record-identity")
        self.assertEqual(item.xml_file_name, "/mnt/sd/actual-file.mp4")
        self.assertEqual(item.display_name, "actual-file.mp4")
        self.assertEqual(item.channel_id, 0)
        self.assertEqual(item.stream_type, "mainStream")
        self.assertEqual(item.file_type, "mp4")
        self.assertEqual(item.record_type, "md")
        self.assertEqual(item.size, 123456)

    def test_file_info_falls_back_without_inventing_present_fields(self):
        xml = """<?xml version="1.0"?>
<body><FileInfoList><FileInfo>
<name>RecM01_20260815_102254_102328.mp4</name>
<startTime><year>2026</year><month>8</month><day>15</day><hour>10</hour><minute>22</minute><second>54</second></startTime>
<endTime><year>2026</year><month>8</month><day>15</day><hour>10</hour><minute>23</minute><second>28</second></endTime>
</FileInfo></FileInfoList></body>"""
        files, _ = probe._parse_file_info_page(xml)
        self.assertEqual(len(files), 1)
        item = files[0]
        self.assertEqual(item.record_id, "")
        self.assertEqual(item.xml_file_name, "")
        self.assertEqual(item.display_name, "RecM01_20260815_102254_102328.mp4")


if __name__ == "__main__":
    unittest.main()
