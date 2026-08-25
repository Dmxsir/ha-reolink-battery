from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
RECOVERY = (
    ROOT
    / "custom_components"
    / "reolink_battery"
    / "recording_worker_v138.py"
)
INIT = ROOT / "custom_components" / "reolink_battery" / "__init__.py"


class RecordingStreamRecoveryV138Tests(unittest.TestCase):
    def test_partial_remote_disconnect_is_narrowly_classified(self) -> None:
        source = RECOVERY.read_text(encoding="utf-8")
        self.assertIn(
            'INCOMPLETE_STREAM_FAILURE_STAGE = "STREAM_REMOTE_DISCONNECT_INCOMPLETE"',
            source,
        )
        self.assertIn("remote_disconnect_observed", source)
        self.assertIn("termination_reason == \"connection_closed\"", source)
        self.assertIn("file_bytes > 0", source)
        self.assertIn("expected_size > file_bytes", source)

    def test_partial_transfer_uses_short_battery_awake_retries(self) -> None:
        source = RECOVERY.read_text(encoding="utf-8")
        self.assertIn(
            "INCOMPLETE_STREAM_RETRY_DELAYS_SECONDS = (3.0, 6.0)", source
        )
        self.assertIn("fast_recovery = True", source)
        self.assertIn("RETRY_DELAYS_SECONDS", source)
        self.assertIn("MAX_ATTEMPTS_PER_TRIGGER", source)

    def test_transport_is_not_reimplemented_by_recovery_layer(self) -> None:
        source = RECOVERY.read_text(encoding="utf-8")
        self.assertNotIn("send_periodic_ack", source)
        self.assertNotIn("_send_p2p_heartbeat", source)
        self.assertNotIn("_build_full_high_cmd8", source)
        self.assertNotIn("_build_cmd13_wire", source)

    def test_integration_loads_recovery_worker(self) -> None:
        source = INIT.read_text(encoding="utf-8")
        self.assertIn(
            "from .recording_worker_v138 import RecordingWorker", source
        )


if __name__ == "__main__":
    unittest.main()
