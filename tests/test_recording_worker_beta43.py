"""Regression guards for beta.43 fresh retry preemption."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"
TRANSPORT = ROOT / "custom_components" / "reolink_battery" / "transport.py"
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"
MANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"


class Beta43FreshRetryPreemptionTests(unittest.TestCase):
    def test_retry_wait_yields_to_newer_motion(self):
        source = WORKER.read_text()
        self.assertIn("def _newer_pending_event", source)
        self.assertIn("async def _wait_retry_or_newer_event", source)
        self.assertIn("await asyncio.wait_for(self._trigger.wait(), timeout=remaining)", source)
        self.assertIn("self._record_retry_preemption(event, newer)", source)
        self.assertIn("preempted = True", source)
        self.assertIn("if not completed and not preempted", source)

    def test_active_download_is_not_cancelled(self):
        source = WORKER.read_text()
        self.assertNotIn(".cancel()", source)
        self.assertIn("if await self._process_once(event):", source)

    def test_previous_attempt_snapshot_survives_next_reset(self):
        source = WORKER.read_text()
        snapshot = source.index("prior_trace = stream_probe_state")
        reset = source.index("reset_stream_probe_state(", snapshot)
        self.assertLess(snapshot, reset)
        for token in (
            "prior_failure_stage",
            "prior_uid_resolve_elapsed_ms",
            "prior_stream_termination_reason",
            "prior_stream_file_bytes",
            "prior_stream_expected_size",
        ):
            self.assertIn(token, source)

    def test_protocol_contracts_remain_beta42(self):
        transport = TRANSPORT.read_text()
        beta20 = BETA20.read_text()
        self.assertIn("UID_RESOLVE_TIMEOUT_SECONDS = 15.0", transport)
        self.assertIn("UID_RESOLVE_RESEND_SECONDS = 0.5", transport)
        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", beta20)
        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", beta20)
        self.assertIn("activate_fresh_heartbeat_tids_after_login", beta20)

    def test_diagnostics_and_version(self):
        diagnostics = DIAGNOSTICS.read_text()
        self.assertIn('"retry_preemption_policy": "newer_notification_before_retry"', diagnostics)
        self.assertIn('"prior_attempt": {', diagnostics)
        version = json.loads(MANIFEST.read_text())["version"]
        self.assertEqual(version, "1.3.7")


if __name__ == "__main__":
    unittest.main()
