"""Regression guards for beta.42 fresh-first queue scheduling."""

from pathlib import Path
import json
import unittest

ROOT = Path(__file__).parents[1]
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
TRANSPORT = ROOT / "custom_components" / "reolink_battery" / "transport.py"
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"
MANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"


class Beta42FreshFirstQueueTests(unittest.TestCase):
    def test_newest_pending_event_is_selected_first(self):
        source = WORKER.read_text()
        self.assertIn("def _next_android_event", source)
        self.assertIn("return max(", source)
        self.assertIn("event.notification_post_time or event.alarm_time", source)
        self.assertNotIn("def _oldest_android_event", source)

    def test_new_notification_rearms_deferred_backlog(self):
        source = WORKER.read_text()
        notify = source[source.index("    def notify"):source.index("    async def async_shutdown")]
        self.assertIn("self._deferred_event_ids.clear()", notify)
        self.assertIn("self.state.deferred_count = 0", notify)
        self.assertIn("self.state.deferred_rearmed_count += 1", notify)

    def test_wake_and_download_protocol_contracts_are_unchanged(self):
        transport = TRANSPORT.read_text()
        beta20 = BETA20.read_text()
        self.assertIn("UID_RESOLVE_TIMEOUT_SECONDS = 15.0", transport)
        self.assertIn("UID_RESOLVE_RESEND_SECONDS = 0.5", transport)
        self.assertNotIn("p2p.reolink.com", transport)
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", beta20)
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", beta20)
        self.assertIn("activate_fresh_heartbeat_tids_after_login", beta20)

    def test_diagnostics_and_version(self):
        diagnostics = DIAGNOSTICS.read_text()
        self.assertIn('"selection_policy": "newest_pending_first"', diagnostics)
        self.assertIn('"deferred_rearm_policy": "new_notification"', diagnostics)
        self.assertIn('"milestone": "3B.16-fresh-first-queue-rearm"', diagnostics)
        self.assertEqual(json.loads(MANIFEST.read_text())["version"], "0.1.2-beta.42")


if __name__ == "__main__":
    unittest.main()
