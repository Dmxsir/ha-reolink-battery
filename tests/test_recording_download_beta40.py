"""Regression guards for beta.40 post-auth fresh heartbeat IDs."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BASE = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"
MANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"


class Beta40PostAuthFreshHeartbeatTests(unittest.TestCase):
    def test_wake_auth_preserves_beta38_heartbeat_identity(self):
        source = BETA20.read_text()
        self.assertIn("self._p2p_heartbeat_fresh_tid_enabled = False", source)
        self.assertIn("self._p2p_heartbeat_pre_auth_reused_tid_count += 1", source)
        self.assertIn("transaction_id = self._p2p_heartbeat_tid", source)

    def test_fresh_ids_activate_only_after_login_success(self):
        source = BETA20.read_text()
        base = BASE.read_text()
        self.assertIn("def activate_fresh_heartbeat_tids_after_login", source)
        self.assertIn("self._p2p_heartbeat_fresh_tid_enabled = True", source)
        login_pos = base.index("await host.baichuan.login()")
        activate_pos = base.index("activate_fresh_heartbeat_tids_after_login")
        self.assertGreater(activate_pos, login_pos)

    def test_beta38_and_beta39_post_auth_transport_contracts_are_preserved(self):
        source = BETA20.read_text()
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)
        self.assertIn("self.udp_immediate_ack_suppressed_count += 1", source)
        self.assertIn("range(self._recv_seq_id + 1, highest + 1)", source)
        self.assertIn("self._p2p_heartbeat_tids.add(transaction_id)", source)
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)
        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)
        beta21 = BETA21.read_text()
        self.assertEqual(beta21.count("_send_reliable_download_packet("), 2)
        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", WORKER.read_text())

    def test_diagnostics_and_version(self):
        diagnostics = DIAGNOSTICS.read_text()
        for token in (
            "p2p_heartbeat_fresh_tid_activated_after_login",
            "p2p_heartbeat_pre_auth_reused_tid_count",
            "3B.14-post-auth-fresh-heartbeat-tid",
        ):
            self.assertIn(token, diagnostics)
        self.assertIn('"version": "0.1.2-beta.40"', MANIFEST.read_text())


if __name__ == "__main__":
    unittest.main()
