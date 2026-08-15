"""Focused beta.20 tests for P2P heartbeat full-transfer diagnostics."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

from reolink_aio.baichuan.util import calc_crc, decrypt_udp_baichuan

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta20_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta20")


class P2PHeartbeatProbeTests(unittest.TestCase):
    def test_p2p_heartbeat_packet_uses_discovery_layer_and_exact_ids(self):
        tid = 123456
        packet = probe._encode_p2p_heartbeat(tid, 111, 222)
        self.assertEqual(packet[:4], probe.beta17.transport_mod.DISCOVERY_MAGIC)
        payload_len = int.from_bytes(packet[4:8], "little")
        self.assertEqual(int.from_bytes(packet[12:16], "little"), tid)
        payload = packet[20 : 20 + payload_len]
        self.assertEqual(calc_crc(payload), packet[16:20])
        xml = decrypt_udp_baichuan(payload, tid)
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        self.assertIn("<C2D_HB>", xml)
        self.assertIn("<cid>111</cid>", xml)
        self.assertIn("<did>222</did>", xml)
        self.assertNotIn("<uid>", xml)

    def test_remote_disconnect_classifier_is_secret_safe(self):
        tid = 654321
        xml = "<P2P><D2C_DISC><cid>1</cid><did>2</did></D2C_DISC></P2P>"
        payload = probe.encrypt_udp_baichuan(xml, tid)
        packet = (
            probe.beta17.transport_mod.DISCOVERY_MAGIC
            + len(payload).to_bytes(4, "little")
            + bytes.fromhex("01000000")
            + tid.to_bytes(4, "little")
            + calc_crc(payload)
            + payload
        )
        self.assertTrue(probe._packet_is_remote_disconnect(packet))
        self.assertFalse(probe._packet_is_remote_disconnect(b"bad"))

    def test_trace_disables_proactive_cmd234_and_enables_p2p_interval(self):
        trace = probe._new_trace(attempted=True)
        self.assertTrue(trace.attempted)
        self.assertFalse(trace.keepalive_attempted)
        self.assertEqual(trace.keepalive_count, 0)
        self.assertEqual(trace.proactive_cmd234_count, 0)
        self.assertEqual(trace.p2p_heartbeat_count, 0)
        self.assertEqual(trace.p2p_heartbeat_interval_seconds, 1.0)
        self.assertFalse(trace.p2p_heartbeat_started_after_handoff)
        self.assertIsNone(trace.p2p_heartbeat_first_delay_seconds)
        self.assertEqual(trace.p2p_heartbeat_pre_cmd13_count, 0)
        self.assertFalse(trace.p2p_heartbeat_background_task_active)
        self.assertEqual(trace.aggregate_limit_bytes, 16 * 1024 * 1024)
        self.assertEqual(
            probe.CONTENT_LAYOUT,
            "cmd13_prepare_cmd8_p2p_heartbeat_full_transfer_shape",
        )

    def test_continuous_heartbeat_trace_captures_pre_cmd13_snapshot(self):
        class _Task:
            @staticmethod
            def done():
                return False

        connection = object.__new__(probe._P2PHeartbeatFullTransferConnection)
        connection._p2p_heartbeat_total_count = 5
        connection._p2p_heartbeat_started_after_handoff = True
        connection._p2p_heartbeat_started_at = 10.0
        connection._p2p_heartbeat_first_sent_at = 10.125
        connection._p2p_heartbeat_task = _Task()

        trace = probe._new_trace(attempted=True)
        connection._apply_p2p_heartbeat_trace(
            trace, snapshot_pre_cmd13=True
        )

        self.assertTrue(trace.p2p_heartbeat_attempted)
        self.assertEqual(trace.p2p_heartbeat_count, 5)
        self.assertTrue(trace.p2p_heartbeat_started_after_handoff)
        self.assertEqual(trace.p2p_heartbeat_first_delay_seconds, 0.125)
        self.assertEqual(trace.p2p_heartbeat_pre_cmd13_count, 5)
        self.assertTrue(trace.p2p_heartbeat_background_task_active)


if __name__ == "__main__":
    unittest.main()
