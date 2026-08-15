"""Regression guard for beta.32 single continuous P2P heartbeat."""

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"


def _function_slice(source: str, marker: str, next_marker: str) -> str:
    start = source.index(marker)
    end = source.index(next_marker, start)
    return source[start:end]


class Beta32SingleHeartbeatTests(unittest.TestCase):
    def test_beta21_download_loop_has_no_local_heartbeat_sender(self):
        source = BETA21.read_text()
        body = _function_slice(
            source,
            "    async def send_file_download_probe(",
            "\n\ndef _build_cmd13_wire",
        )
        self.assertNotIn("next_p2p_heartbeat_at", body)
        self.assertNotIn("self._send_p2p_heartbeat()", body)
        self.assertIn(
            "self._apply_p2p_heartbeat_trace(trace, snapshot_pre_cmd13=True)",
            body,
        )
        self.assertIn("self._apply_p2p_heartbeat_trace(trace)", body)

    def test_beta20_background_sender_starts_after_handoff(self):
        source = BETA20.read_text()
        self.assertIn("def _start_p2p_heartbeat_loop", source)
        self.assertIn("self._record_p2p_heartbeat()", source)
        self.assertIn("await self._stop_p2p_heartbeat_loop()", source)
        self.assertNotIn("next_p2p_heartbeat_at = started_at", source)


if __name__ == "__main__":
    unittest.main()
