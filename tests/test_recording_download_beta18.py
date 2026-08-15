"""Focused beta.18 tests for cmd13 prepare followed by cmd8 media probe."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta18_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta18")
recording_probe = importlib.import_module(f"{PACKAGE}.recording_probe")


class _FakeBaichuan:
    def __init__(self, mess_id: int = 5) -> None:
        self._mess_id = mess_id
        self._connection = None

    @staticmethod
    def _aes_encrypt(data: bytes) -> bytes:
        return data


def _candidate():
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


class Cmd8ProbeTests(unittest.TestCase):
    def test_cmd8_reuses_accepted_cmd13_body_with_fresh_msgnum(self):
        bc = _FakeBaichuan()
        cmd13, meta, _ = probe._beta16_build_cmd13_wire(
            bc, "ABC123", _candidate()
        )
        self.assertEqual(meta.msg_num, 6)
        cmd8, msg_num, body_length, payload_offset = (
            probe._build_cmd8_wire_from_cmd13(bc, cmd13)
        )
        self.assertEqual(msg_num, 7)
        self.assertEqual(bc._mess_id, 7)
        self.assertEqual(int.from_bytes(cmd8[4:8], "little"), 8)
        self.assertEqual(cmd8[12], 7)
        self.assertEqual(cmd8[13], 0)
        self.assertEqual(int.from_bytes(cmd8[14:16], "little"), 7)
        self.assertEqual(int.from_bytes(cmd8[18:20], "little"), 0x6482)
        self.assertEqual(payload_offset, 0)
        self.assertEqual(int.from_bytes(cmd8[20:24], "little"), 0)
        self.assertEqual(body_length, len(cmd13) - 24)
        self.assertEqual(cmd8[24:], cmd13[24:])

    def test_trace_is_bounded_and_secret_safe(self):
        trace = probe._new_trace(attempted=True)
        self.assertTrue(trace.attempted)
        self.assertEqual(trace.sample_limit_bytes, 2 * 1024 * 1024)
        self.assertEqual(trace.sample_limit_frames, 512)
        self.assertFalse(trace.cmd8_attempted)
        self.assertFalse(trace.cmd8_media_observed)
        self.assertFalse(trace.raw_values_exposed)
        self.assertEqual(
            probe.CONTENT_LAYOUT,
            "cmd13_prepare_then_cmd8_id_only_no_extension_media_shape",
        )


if __name__ == "__main__":
    unittest.main()
