"""Focused beta.17 tests for same-session cmd13 stream-shape sampling."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta17_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta17")
recording_probe = importlib.import_module(f"{PACKAGE}.recording_probe")


class _FakeBaichuan:
    def __init__(self, mess_id: int = 5) -> None:
        self._mess_id = mess_id
        self._connection = None

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


class DownloadStreamShapeProbeTests(unittest.TestCase):
    def test_beta17_keeps_beta16_id_only_no_extension_wire(self):
        wire, meta, _ = probe._build_cmd13_wire(
            _FakeBaichuan(), "ABC123", _argus_candidate()
        )
        self.assertEqual(wire[:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 13)
        self.assertEqual(wire[12], 7)
        self.assertEqual(wire[13], 0)
        self.assertEqual(int.from_bytes(wire[14:16], "little"), 6)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6482)
        self.assertEqual(meta.payload_offset, 0)
        payload = wire[24:].decode("utf-8")
        self.assertIn("<channelId>0</channelId>", payload)
        self.assertIn("<Id>/mnt/sda/recording.mp4</Id>", payload)
        self.assertNotIn("<uid>", payload)
        self.assertNotIn("<fileName>", payload)
        self.assertNotIn("<name>", payload)
        self.assertNotIn("<streamType>", payload)
        self.assertNotIn("<recordType>", payload)

    def test_xml_inspection_exposes_only_shape_and_size(self):
        trace = probe.StreamProbeTrace(attempted=True)
        xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b"<body><FileInfoList><FileInfo>"
            b"<Id>/secret/path.mp4</Id><name>secret.mp4</name>"
            b"<sizeL>10521742</sizeL><sizeH>0</sizeH>"
            b"<handle>opaque-secret-handle</handle>"
            b"</FileInfo></FileInfoList></body>"
        )
        probe._inspect_xml(xml, trace)
        self.assertTrue(trace.xml_file_info_present)
        self.assertTrue(trace.xml_handle_present)
        self.assertEqual(trace.xml_reported_size, 10521742)
        self.assertFalse(trace.raw_values_exposed)

    def test_media_signature_helpers(self):
        mp4 = (24).to_bytes(4, "big") + b"ftypmp42" + b"\x00" * 12
        self.assertTrue(probe._looks_like_mp4(mp4))
        self.assertTrue(probe._looks_like_bcmedia(b"1001" + b"\x00" * 16))
        self.assertTrue(probe._looks_like_bcmedia(b"1002" + b"\x00" * 16))
        self.assertFalse(probe._looks_like_bcmedia(mp4))

    def test_probe_limits_and_marker(self):
        trace = probe.StreamProbeTrace()
        self.assertEqual(trace.sample_limit_bytes, 2 * 1024 * 1024)
        self.assertEqual(trace.sample_limit_frames, 256)
        self.assertEqual(
            probe.CONTENT_LAYOUT,
            "fileinfo_id_only_no_extension_stream_shape",
        )


if __name__ == "__main__":
    unittest.main()
