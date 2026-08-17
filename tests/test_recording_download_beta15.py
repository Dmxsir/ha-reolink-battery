"""Focused beta.15 tests for the minimal Id-only cmd13 FileInfo strategy."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta15_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta15")
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


class DownloadIdOnlyProbeTests(unittest.TestCase):
    def test_payload_contains_only_channel_and_id_file_fields(self):
        wire, meta, _ = probe._build_cmd13_wire(
            _FakeBaichuan(), "ABC123", _argus_candidate()
        )
        payload = wire[24 + meta.payload_offset :].decode("utf-8")
        self.assertIn("<channelId>0</channelId>", payload)
        self.assertIn("<Id>/mnt/sda/recording.mp4</Id>", payload)
        self.assertNotIn("<uid>", payload)
        self.assertNotIn("<fileName>", payload)
        self.assertNotIn("<name>", payload)
        self.assertNotIn("<streamType>", payload)
        self.assertNotIn("<fileType>", payload)
        self.assertNotIn("<recordType>", payload)
        self.assertNotIn("<startTime>", payload)
        self.assertNotIn("<endTime>", payload)

    def test_binary_extension_and_framing_remain_beta14(self):
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
        extension = wire[24 : 24 + meta.payload_offset].decode("utf-8")
        self.assertIn("<binaryData>1</binaryData>", extension)

    def test_content_layout_marker(self):
        self.assertEqual(probe.CONTENT_LAYOUT, "fileinfo_id_only")


if __name__ == "__main__":
    unittest.main()
