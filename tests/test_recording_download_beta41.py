"""Regression guards for beta.41 UID/LAN wake reliability."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
TRANSPORT = ROOT / "custom_components" / "reolink_battery" / "transport.py"
BASE = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe.py"
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"
MANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"


class Beta41UidWakeReliabilityTests(unittest.TestCase):
    def test_uid_discovery_uses_neolink_compatible_window_and_cadence(self):
        source = TRANSPORT.read_text()
        self.assertIn("UID_RESOLVE_TIMEOUT_SECONDS = 15.0", source)
        self.assertIn("UID_RESOLVE_RESEND_SECONDS = 0.5", source)
        self.assertIn("timeout: float = UID_RESOLVE_TIMEOUT_SECONDS", source)
        self.assertIn("next_send = now + UID_RESOLVE_RESEND_SECONDS", source)

    def test_uid_discovery_trace_is_secret_safe_and_propagated(self):
        transport = TRANSPORT.read_text()
        base = BASE.read_text()
        worker = WORKER.read_text()
        diagnostics = DIAGNOSTICS.read_text()
        for token in (
            "class UidResolveTrace",
            "send_rounds",
            "datagrams_sent",
            "elapsed_ms",
            "succeeded",
        ):
            self.assertIn(token, transport)
        self.assertIn("uid_resolve_trace = UidResolveTrace", base)
        self.assertIn("resolve_uid_lan, uid, interface, resolve_timeout, uid_resolve_trace", base)
        self.assertIn("last_uid_resolve_send_rounds", worker)
        self.assertIn('"network_identifiers_exposed": False', diagnostics)

    def test_beta40_download_transport_is_untouched(self):
        source = BETA20.read_text()
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)
        self.assertIn("self._p2p_heartbeat_fresh_tid_enabled = False", source)
        self.assertIn("activate_fresh_heartbeat_tids_after_login", source)
        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", WORKER.read_text())

    def test_diagnostics_and_version(self):
        diagnostics = DIAGNOSTICS.read_text()
        self.assertIn('"milestone": "3B.15-uid-wake-reliability"', diagnostics)
        self.assertIn('"uid_resolve": {', diagnostics)
        self.assertIn('"version": "0.1.2-beta.41"', MANIFEST.read_text())


if __name__ == "__main__":
    unittest.main()
