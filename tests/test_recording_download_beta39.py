"""Regression guards for beta.39 fresh P2P heartbeat transaction IDs."""

import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"


class Beta39FreshHeartbeatTidTests(unittest.TestCase):
    def test_fresh_heartbeat_transaction_id_capability_is_preserved(self):
        source = BETA20.read_text()
        self.assertIn("self._p2p_heartbeat_tids: set[int] = set()", source)
        self.assertIn("transaction_id = secrets.randbelow(999_000) + 1_000", source)
        self.assertIn("transaction_id == self._p2p_heartbeat_tid", source)
        self.assertIn("transaction_id in self._p2p_heartbeat_tids", source)
        self.assertIn("self._p2p_heartbeat_tids.add(transaction_id)", source)
        self.assertIn("p2p_heartbeat_unique_tid_count", source)

    def test_beta38_periodic_only_ack_contract_is_preserved(self):
        source = BETA20.read_text()
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)
        self.assertIn("def _send_ack_now(self) -> bool:", source)
        self.assertIn("self.udp_immediate_ack_suppressed_count += 1", source)
        self.assertIn("range(self._recv_seq_id + 1, highest + 1)", source)

    def test_existing_download_transport_is_unchanged(self):
        source = BETA20.read_text()
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)
        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)
        beta21 = BETA21.read_text()
        self.assertEqual(beta21.count("_send_reliable_download_packet("), 2)
        self.assertIn("protocol=protocol", beta21)
        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", WORKER.read_text())

    def test_diagnostics_preserve_fresh_tid_telemetry(self):
        diagnostics = DIAGNOSTICS.read_text()
        for token in (
            "p2p_heartbeat_fresh_tid_enabled",
            "p2p_heartbeat_unique_tid_count",
        ):
            self.assertIn(token, diagnostics)


if __name__ == "__main__":
    unittest.main()
