"""Regression guards for beta.34 disconnect-safe UDP telemetry."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"


class Beta34RxTelemetryTests(unittest.TestCase):
    def test_beta21_final_snapshot_uses_saved_protocol_reference(self):
        source = BETA21.read_text()
        self.assertIn(
            "self._apply_udp_reliability_trace(trace, protocol=protocol)",
            source,
        )

    def test_beta20_snapshot_accepts_explicit_protocol(self):
        source = BETA20.read_text()
        self.assertIn(
            "protocol: _P2PHeartbeatProbeProtocol | None = None",
            source,
        )
        self.assertIn("snapshot_protocol = protocol", source)
        self.assertIn("udp_snapshot_from_local_protocol", source)

    def test_beta33_transport_parameters_are_unchanged(self):
        source = BETA20.read_text()
        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)
        self.assertIn("transport.sendto(packet, (self._host, self._port))", source)


if __name__ == "__main__":
    unittest.main()
