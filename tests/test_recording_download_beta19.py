"""Focused beta.19 tests for keepalive full-transfer diagnostics."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta19_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta19")


class FullTransferProbeTests(unittest.TestCase):
    def test_keepalive_wire_matches_cmd234_channel0_msg0_modern24(self):
        wire = probe._build_keepalive_wire()
        self.assertEqual(len(wire), 24)
        self.assertEqual(wire[:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 234)
        self.assertEqual(int.from_bytes(wire[8:12], "little"), 0)
        self.assertEqual(wire[12], 0)
        self.assertEqual(wire[13], 0)
        self.assertEqual(int.from_bytes(wire[14:16], "little"), 0)
        self.assertEqual(int.from_bytes(wire[16:18], "little"), 0)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6414)
        self.assertEqual(int.from_bytes(wire[20:24], "little"), 0)

    def test_embedded_mp4_offset(self):
        prefix = b"1001" + b"x" * 138
        mp4 = (24).to_bytes(4, "big") + b"ftypmp42" + b"\x00" * 12
        self.assertEqual(probe._find_mp4_offset(prefix + mp4), len(prefix))
        self.assertIsNone(probe._find_mp4_offset(b"no media"))

    def test_trace_limits_and_keepalive_cadence(self):
        trace = probe._new_trace(attempted=True)
        self.assertTrue(trace.attempted)
        self.assertEqual(trace.sample_limit_bytes, 16 * 1024 * 1024)
        self.assertEqual(trace.aggregate_limit_bytes, 16 * 1024 * 1024)
        self.assertEqual(trace.sample_limit_frames, 4096)
        self.assertEqual(trace.keepalive_interval_seconds, 0.75)
        self.assertFalse(trace.expected_size_reached)
        self.assertFalse(trace.raw_values_exposed)
        self.assertEqual(
            probe.CONTENT_LAYOUT,
            "cmd13_prepare_cmd8_keepalive_full_transfer_shape",
        )


if __name__ == "__main__":
    unittest.main()
