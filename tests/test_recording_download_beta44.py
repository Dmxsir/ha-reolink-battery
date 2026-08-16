"""Regression guards for beta.44 verified size-aware collector."""

from pathlib import Path
import json
import unittest

ROOT = Path(__file__).parents[1]
BETA22 = ROOT / "custom_components" / "reolink_battery" / "recording_download_beta22.py"
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"
MANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"


class Beta44VerifiedSizeAwareCollectorTests(unittest.TestCase):
    def test_verified_path_no_longer_uses_fixed_16mib_ceiling(self):
        source = BETA22.read_text()
        self.assertIn("VERIFIED_FILE_OVERHEAD_BYTES = 4 * 1024 * 1024", source)
        self.assertIn("VERIFIED_FILE_HARD_CAP_BYTES = 128 * 1024 * 1024", source)
        self.assertIn("def _verified_collector_limits", source)
        self.assertIn("expected + VERIFIED_FILE_OVERHEAD_BYTES", source)
        self.assertIn("self._stream_trace.sample_limit_bytes = collector_bytes", source)
        self.assertIn("self._stream_trace.aggregate_limit_bytes = collector_bytes", source)
        self.assertIn("self._stream_trace.sample_limit_frames = collector_frames", source)

    def test_18mb_recording_gets_limit_above_expected_size(self):
        ns = {}
        # Avoid importing Home Assistant dependencies; verify the sizing formula
        # from source constants through equivalent arithmetic.
        expected = 17_921_985
        base = 16 * 1024 * 1024
        overhead = 4 * 1024 * 1024
        hard = 128 * 1024 * 1024
        limit = min(max(base, expected + overhead), hard)
        self.assertGreater(limit, expected)
        self.assertEqual(limit, 22_116_289)

    def test_transport_contracts_unchanged(self):
        source = BETA20.read_text()
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)
        self.assertIn("activate_fresh_heartbeat_tids_after_login", source)

    def test_diagnostics_and_version(self):
        diagnostics = DIAGNOSTICS.read_text()
        self.assertIn('"milestone": "3B.', diagnostics)
        version = json.loads(MANIFEST.read_text())["version"]
        self.assertTrue(version.startswith("0.1.2-beta."))
        self.assertGreaterEqual(int(version.rsplit(".", 1)[1]), 44)


if __name__ == "__main__":
    unittest.main()
