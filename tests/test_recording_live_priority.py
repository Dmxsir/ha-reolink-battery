from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
LIVE_HTTP = ROOT / "custom_components" / "reolink_battery" / "live_http.py"
WORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker_v138.py"


class RecordingLivePriorityTests(unittest.TestCase):
    def test_live_subscribe_is_gated_during_recording_priority(self) -> None:
        source = LIVE_HTTP.read_text(encoding="utf-8")
        self.assertIn("self._recording_priority_depth == 0", source)
        self.assertIn("async def async_pause_for_recording", source)
        self.assertIn("async def async_resume_after_recording", source)
        self.assertIn("self._stop_event.set()", source)
        self.assertIn("recording_preemptions", source)

    def test_worker_wraps_base_attempt_with_live_priority(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        worker_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RecordingWorker"
        )
        process_once = next(
            node
            for node in worker_class.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == "_process_once"
        )
        segment = ast.get_source_segment(source, process_once) or ""
        pause_pos = segment.find("await pause()")
        base_pos = segment.find("await super()._process_once(event)")
        resume_pos = segment.rfind("await resume()")
        self.assertGreaterEqual(pause_pos, 0)
        self.assertGreater(base_pos, pause_pos)
        self.assertGreater(resume_pos, base_pos)
        self.assertIn("finally:", segment)

    def test_transport_is_still_not_reimplemented_in_priority_layer(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertNotIn("send_periodic_ack", source)
        self.assertNotIn("_send_p2p_heartbeat", source)
        self.assertNotIn("_build_full_high_cmd8", source)
        self.assertNotIn("_build_cmd13_wire", source)


if __name__ == "__main__":
    unittest.main()
