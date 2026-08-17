"""Regression guards for beta.36 periodic receive ACK cadence."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"


class Beta36PeriodicRxAckTests(unittest.TestCase):
    def test_periodic_ack_uses_existing_ack_encoder(self):
        source = BETA20.read_text()
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)
        self.assertIn("def send_periodic_ack(self) -> bool:", source)
        self.assertIn("self.send_ack()", source)
        self.assertIn("await asyncio.sleep(PERIODIC_RX_ACK_INTERVAL)", source)
        self.assertIn("protocol.send_periodic_ack()", source)

    def test_periodic_ack_has_single_connection_lifetime_task(self):
        source = BETA20.read_text()
        self.assertIn("self._periodic_rx_ack_task: asyncio.Task[None] | None = None", source)
        self.assertIn("self._start_periodic_rx_ack_loop()", source)
        self.assertIn("await self._stop_periodic_rx_ack_loop()", source)

    def test_existing_transport_contract_is_unchanged(self):
        source = BETA20.read_text()
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)
        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)
        beta21 = BETA21.read_text()
        self.assertIn("cmd8_wire, cmd_id=8, response_future=cmd8_delivery", beta21)
        worker = WORKER.read_text()
        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", worker)

    def test_diagnostics_expose_periodic_ack_telemetry(self):
        source = DIAGNOSTICS.read_text()
        for token in (
            "udp_periodic_ack_started",
            "udp_periodic_ack_interval_ms",
            "udp_periodic_ack_count",
            "udp_periodic_ack_gap_count",
            "3B.10-periodic-rx-ack",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
