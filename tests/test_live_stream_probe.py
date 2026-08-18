"""Focused tests for the experimental bounded Baichuan live-view probe."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_live_stream_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.live_stream_probe")
state_mod = importlib.import_module(f"{PACKAGE}.live_stream_diagnostics")


class _FakeBaichuan:
    def __init__(self, mess_id: int = 5) -> None:
        self._mess_id = mess_id

    @staticmethod
    def _aes_encrypt(data: bytes) -> bytes:
        return data


class LiveStreamProbeTests(unittest.TestCase):
    def test_main_stream_cmd3_layout_matches_baichuan_preview(self):
        wire, meta = probe._build_preview_wire(
            _FakeBaichuan(), cmd_id=probe.LIVE_START_CMD_ID, stream="main"
        )
        payload = wire[24:].decode("utf-8")

        self.assertEqual(wire[:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 3)
        self.assertEqual(wire[12], 0)
        self.assertEqual(wire[13], 0)
        self.assertEqual(int.from_bytes(wire[14:16], "little"), 6)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6414)
        self.assertEqual(int.from_bytes(wire[20:24], "little"), 0)
        self.assertEqual(meta.preview_handle, 0)
        self.assertEqual(meta.preview_stream_type, "mainStream")
        self.assertIn("<channelId>0</channelId>", payload)
        self.assertIn("<handle>0</handle>", payload)
        self.assertIn("<streamType>mainStream</streamType>", payload)

    def test_cmd4_reuses_stream_msg_num_and_omits_stream_type_xml(self):
        fake = _FakeBaichuan()
        _, start = probe._build_preview_wire(
            fake, cmd_id=probe.LIVE_START_CMD_ID, stream="main"
        )
        wire, stop = probe._build_preview_wire(
            fake,
            cmd_id=probe.LIVE_STOP_CMD_ID,
            stream="main",
            msg_num=start.msg_num,
        )
        payload = wire[24:].decode("utf-8")

        self.assertEqual(int.from_bytes(wire[4:8], "little"), 4)
        self.assertEqual(stop.msg_num, start.msg_num)
        self.assertEqual(stop.preview_handle, 0)
        self.assertIsNone(stop.preview_stream_type)
        self.assertNotIn("<streamType>", payload)

    def test_sub_stream_layout_uses_code_one_and_handle_256(self):
        wire, meta = probe._build_preview_wire(
            _FakeBaichuan(), cmd_id=probe.LIVE_START_CMD_ID, stream="sub"
        )
        self.assertEqual(wire[13], 1)
        self.assertEqual(meta.preview_handle, 256)
        self.assertEqual(meta.preview_stream_type, "subStream")

    def test_bcmedia_markers_classify_info_and_video_frames(self):
        self.assertEqual(probe._bcmedia_marker(b"1001"), ("info", None))
        self.assertEqual(
            probe._bcmedia_marker(b"00dcH264payload"), ("iframe", "H264")
        )
        self.assertEqual(
            probe._bcmedia_marker(b"10dcH265payload"), ("pframe", "H265")
        )
        self.assertIsNone(probe._bcmedia_marker(b"not-media"))

    def test_diagnostics_state_reset_is_secret_safe_and_deterministic(self):
        entry_id = "live-test-entry"
        state_mod.reset_live_probe_state(entry_id, stream_kind="main")
        state = state_mod.live_probe_state(entry_id)
        self.assertTrue(state.attempted)
        self.assertFalse(state.success)
        self.assertEqual(state.stream_kind, "main")
        self.assertEqual(state.total_body_bytes, 0)
        self.assertFalse(state.bcmedia_observed)


if __name__ == "__main__":
    unittest.main()
