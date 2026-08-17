"""Focused beta.13 tests for the recordType-only cmd13 content probe."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta13_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe")
recording_probe = importlib.import_module(f"{PACKAGE}.recording_probe")


class _FakeBaichuan:
    def __init__(self, mess_id: int = 5) -> None:
        self._mess_id = mess_id

    @staticmethod
    def _aes_encrypt(data: bytes) -> bytes:
        return data


def _candidate(**overrides):
    values = {
        "file_name": "/mnt/sda/recording.mp4",
        "start_time": datetime(2026, 8, 15, 10, 22, 54),
        "end_time": datetime(2026, 8, 15, 10, 23, 28),
        "size": 0,
        "distance_seconds": 4.792,
        "record_id": "/mnt/sda/recording.mp4",
        "xml_file_name": "",
        "display_name": "recording.mp4",
        "channel_id": 0,
        "stream_type": "mainStream",
        "file_type": "mp4",
        "record_type": "io, md",
    }
    values.update(overrides)
    return recording_probe.RecordingCandidate(**values)


class DownloadRecordTypeProbeTests(unittest.TestCase):
    def test_camera_record_type_is_added_without_time_fields(self):
        wire, meta, _ = probe._build_cmd13_wire(
            _FakeBaichuan(), "ABC123", _candidate()
        )
        payload = wire[24 + meta.payload_offset :].decode("utf-8")
        self.assertIn("<streamType>mainStream</streamType>", payload)
        self.assertIn("<fileType>mp4</fileType>", payload)
        self.assertIn("<recordType>io, md</recordType>", payload)
        self.assertNotIn("<startTime>", payload)
        self.assertNotIn("<endTime>", payload)

    def test_record_type_is_xml_escaped(self):
        xml = probe._download_xml(
            "ABC123",
            channel_id=0,
            record_id="id",
            file_name="file.mp4",
            display_name="file.mp4",
            stream_type="mainStream",
            file_type="mp4",
            record_type="io & md",
        )
        self.assertIn("<recordType>io &amp; md</recordType>", xml)

    def test_record_type_remains_optional(self):
        xml = probe._download_xml(
            "ABC123",
            channel_id=0,
            record_id="id",
            file_name="file.mp4",
            display_name="file.mp4",
            stream_type="mainStream",
            file_type="mp4",
        )
        self.assertNotIn("<recordType>", xml)

    def test_framing_stays_identical(self):
        wire, meta, _ = probe._build_cmd13_wire(
            _FakeBaichuan(), "ABC123", _candidate()
        )
        self.assertEqual(wire[:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 13)
        self.assertEqual(wire[12], 7)
        self.assertEqual(wire[13], 0)
        self.assertEqual(int.from_bytes(wire[14:16], "little"), 6)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6482)
        self.assertEqual(meta.payload_offset, len(probe._binary_extension_xml().encode("utf-8")))

    def test_content_layout_marker(self):
        self.assertEqual(
            probe.CONTENT_LAYOUT,
            "fileinfo_identity_plus_stream_type_file_type_record_type",
        )


if __name__ == "__main__":
    unittest.main()
