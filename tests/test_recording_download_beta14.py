"""Focused beta.14 tests for adding only FileInfo start/end time."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta14_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta14")
recording_probe = importlib.import_module(f"{PACKAGE}.recording_probe")


class _FakeBaichuan:
    def __init__(self, mess_id: int = 5) -> None:
        self._mess_id = mess_id

    @staticmethod
    def _aes_encrypt(data: bytes) -> bytes:
        return data


def _argus_candidate():
    return recording_probe.RecordingCandidate(
        file_name="/mnt/sda/recording.mp4",
        start_time=datetime(2026, 8, 15, 10, 22, 54),
        end_time=datetime(2026, 8, 15, 10, 23, 28),
        size=0,
        distance_seconds=4.792,
        record_id="/mnt/sda/recording.mp4",
        xml_file_name="",
        display_name="recording.mp4",
        channel_id=0,
        stream_type="mainStream",
        file_type="mp4",
        record_type="io, md",
    )


class DownloadTimeProbeTests(unittest.TestCase):
    def test_beta14_adds_only_nested_start_and_end_time(self):
        wire, meta, _ = probe._build_cmd13_wire(
            _FakeBaichuan(), "ABC123", _argus_candidate()
        )
        payload = wire[24 + meta.payload_offset :].decode("utf-8")
        self.assertIn("<streamType>mainStream</streamType>", payload)
        self.assertIn("<fileType>mp4</fileType>", payload)
        self.assertIn("<recordType>io, md</recordType>", payload)
        self.assertIn("<startTime>", payload)
        self.assertIn("<year>2026</year><month>8</month><day>15</day>", payload)
        self.assertIn("<hour>10</hour><minute>22</minute><second>54</second>", payload)
        self.assertIn("</startTime>", payload)
        self.assertIn("<endTime>", payload)
        self.assertIn("<hour>10</hour><minute>23</minute><second>28</second>", payload)
        self.assertIn("</endTime>", payload)

    def test_beta14_framing_remains_beta13(self):
        wire, meta, _ = probe._build_cmd13_wire(
            _FakeBaichuan(), "ABC123", _argus_candidate()
        )
        self.assertEqual(wire[:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 13)
        self.assertEqual(wire[12], 7)
        self.assertEqual(wire[13], 0)
        self.assertEqual(int.from_bytes(wire[14:16], "little"), 6)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6482)
        self.assertEqual(
            meta.payload_offset,
            len(probe.base._binary_extension_xml().encode("utf-8")),
        )

    def test_content_layout_marker(self):
        self.assertEqual(
            probe.CONTENT_LAYOUT,
            "fileinfo_identity_plus_stream_type_file_type_record_type_start_end",
        )


if __name__ == "__main__":
    unittest.main()
