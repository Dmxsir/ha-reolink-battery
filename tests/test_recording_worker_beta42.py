"""Regression guards for beta.42 fresh-first queue scheduling."""

import json
import unittest
from pathlib import Path

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

    def test_new_notification_activates_only_its_event(self):
        source = WORKER.read_text()
        notify = source[source.index("    def notify"):source.index("    async def async_shutdown")]
        self.assertIn("self._activated_event_ids.add(event_id)", notify)
        self.assertNotIn(".clear()", notify)

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
        self.assertIn('"selection_policy": "newest_activated_fresh_first"', diagnostics)
        self.assertIn('"deferred_rearm_policy": "explicit_only"', diagnostics)
        version = json.loads(MANIFEST.read_text())["version"]
        self.assertEqual(version, "1.3.7")


if __name__ == "__main__":
    unittest.main()
