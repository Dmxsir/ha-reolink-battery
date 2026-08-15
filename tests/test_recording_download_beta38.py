"""Regression guards for beta.38 periodic-only receive ACK sending."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"


class Beta38PeriodicOnlyRxAckTests(unittest.TestCase):
    def test_periodic_task_is_only_wire_ack_sender(self):
        source = BETA20.read_text()
        self.assertIn("def _send_ack_now(self) -> bool:", source)
        self.assertIn("def send_ack(self) -> None:", source)
        self.assertIn("self.udp_immediate_ack_suppressed_count += 1", source)
        self.assertIn("if not self._send_ack_now():", source)
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)

    def test_inclusive_highest_bitmap_is_preserved(self):
        source = BETA20.read_text()
        self.assertIn("range(self._recv_seq_id + 1, highest + 1)", source)
        self.assertIn("bytes.fromhex(MAGIC_UDP_ACK)", source)
        self.assertIn("udp_ack_inclusive_highest_count", source)

    def test_existing_transport_contract_is_unchanged(self):
        source = BETA20.read_text()
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)
        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)
        beta21 = BETA21.read_text()
        self.assertEqual(beta21.count("_send_reliable_download_packet("), 2)
        self.assertIn("protocol=protocol", beta21)
        worker = WORKER.read_text()
        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", worker)

    def test_periodic_only_diagnostics_contract_remains_exposed(self):
        diagnostics = DIAGNOSTICS.read_text()
        for token in (
            "udp_periodic_only_ack_enabled",
            "udp_immediate_ack_suppressed_count",
        ):
            self.assertIn(token, diagnostics)


if __name__ == "__main__":
    unittest.main()
