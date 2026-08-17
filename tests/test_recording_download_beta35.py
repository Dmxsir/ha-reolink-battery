"""Regression guards for beta.35 gap recovery telemetry."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"


class Beta35GapRecoveryTelemetryTests(unittest.TestCase):
    def test_tracks_real_network_datagrams_and_gap_recovery(self):
        source = BETA20.read_text()
        for token in (
            "udp_network_bc_datagrams_received",
            "udp_recovered_missing_packet_count",
            "udp_unresolved_missing_packet_count_at_disconnect",
            "udp_buffered_out_of_order_at_disconnect",
            "udp_expected_next_seq_at_disconnect",
            "udp_max_gap_recovery_ms",
        ):
            self.assertIn(token, source)
        self.assertIn("def datagram_received(self, data: bytes, addr: tuple[str, int])", source)
        self.assertIn("self._missing_seq_ids_seen - self._recovered_missing_seq_ids", source)

    def test_transport_timing_and_retransmit_limits_unchanged(self):
        source = BETA20.read_text()
        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)

    def test_beta21_still_uses_saved_protocol_snapshot(self):
        source = BETA21.read_text()
        self.assertIn("self._apply_udp_reliability_trace(trace, protocol=protocol)", source)


if __name__ == "__main__":
    unittest.main()
