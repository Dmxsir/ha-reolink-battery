"""Regression guards for beta.37 inclusive receive ACK bitmap."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"


class Beta37InclusiveRxAckTests(unittest.TestCase):
    def test_ack_bitmap_includes_highest_buffered_sequence(self):
        source = BETA20.read_text()
        self.assertIn("for seq_id in range(self._recv_seq_id + 1, highest + 1):", source)
        self.assertIn("bytes.fromhex(MAGIC_UDP_ACK)", source)
        self.assertIn("udp_header + bytes(payload)", source)
        self.assertIn("udp_ack_inclusive_highest_count", source)

    def test_periodic_ack_and_existing_transport_remain_enabled(self):
        source = BETA20.read_text()
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)
        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)
        self.assertIn("protocol.send_periodic_ack()", source)
        beta21 = BETA21.read_text()
        self.assertEqual(beta21.count("_send_reliable_download_packet("), 2)
        self.assertIn("protocol=protocol", beta21)
        worker = WORKER.read_text()
        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", worker)

    def test_current_gap_snapshot_is_not_cumulative_counter(self):
        source = BETA20.read_text()
        self.assertIn("udp_current_missing_packet_count_at_disconnect", source)
        self.assertIn("current_window - set(self._seq_data)", source)

    def test_beta37_diagnostics_contract_survives_later_versions(self):
        diagnostics = DIAGNOSTICS.read_text()
        for token in (
            "udp_ack_inclusive_highest_enabled",
            "udp_ack_inclusive_highest_count",
            "udp_current_missing_packet_count_at_disconnect",
        ):
            self.assertIn(token, diagnostics)


if __name__ == "__main__":
    unittest.main()
