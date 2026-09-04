"""Regression guards for beta.45 cmd13 reported-size collector resize."""

import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
BETA22 = ROOT / "custom_components" / "reolink_battery" / "recording_download_beta22.py"
DIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"


class Beta45Cmd13ReportedSizeCollectorTests(unittest.TestCase):
    def test_cmd13_reported_size_resizes_before_cmd8_collection(self):
        source = BETA22.read_text()
        self.assertIn("def _apply_reported_size_collector_limits", source)
        self.assertIn("self._stream_trace.xml_reported_size", source)
        self.assertIn("if frame.cmd_id == 13 and frame.response_code in (0, 200):", source)
        self.assertIn("self._apply_reported_size_collector_limits()", source)
        self.assertIn("await self._async_finalize_collected_file()", source)
        observe = source[
            source.index("    def _observe_frame") : source.index(
                "    def _release_collected_media"
            )
        ]
        self.assertNotIn("open(", observe)
        self.assertNotIn("write(", observe)

    def test_argus_17921985_cmd13_size_gets_22116289_limit(self):
        expected = 17_921_985
        base = 16 * 1024 * 1024
        overhead = 4 * 1024 * 1024
        hard = 128 * 1024 * 1024
        limit = min(max(base, expected + overhead), hard)
        self.assertEqual(limit, 22_116_289)
        self.assertGreater(limit, expected)

    def test_zero_candidate_size_is_safe_until_cmd13_reports_size(self):
        base = 16 * 1024 * 1024
        overhead = 4 * 1024 * 1024
        hard = 128 * 1024 * 1024
        candidate_limit = min(max(base, 0 + overhead), hard)
        cmd13_limit = min(max(base, 17_921_985 + overhead), hard)
        self.assertEqual(candidate_limit, base)
        self.assertGreater(cmd13_limit, candidate_limit)

    def test_diagnostics_preserve_reported_size_telemetry(self):
        diagnostics = DIAGNOSTICS.read_text()
        self.assertIn('"xml_reported_size"', diagnostics)
        self.assertIn('"expected_size_reached"', diagnostics)


if __name__ == "__main__":
    unittest.main()
