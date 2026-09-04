"""Regression guards for v1.2.1 persistent recording dedupe."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMP = ROOT / "custom_components" / "reolink_battery"


class RecordingDedupeV121Tests(unittest.TestCase):
    def test_version(self):
        manifest = json.loads((COMP / "manifest.json").read_text())
        version = tuple(int(part) for part in manifest["version"].split("."))
        self.assertGreaterEqual(version, (1, 2, 1))

    def test_persistent_fingerprint_store(self):
        events = (COMP / "events.py").read_text()
        coordinator = (COMP / "coordinator.py").read_text()
        self.assertIn("class CompletedRecording", events)
        self.assertIn('\"completed_recordings\"', events)
        self.assertIn("completed_recording_fingerprints", events)
        self.assertIn("remember_completed_recording", events)
        self.assertIn("completed_recording: CompletedRecording | None = None", coordinator)

    def test_candidate_dedupe_happens_before_cmd13(self):
        source = (COMP / "recording_download_probe.py").read_text()
        gate = source.index("candidate_fingerprint in completed_recording_fingerprints")
        cmd13 = source.index("_build_cmd13_wire(host.baichuan, uid, candidate)", gate)
        self.assertLess(gate, cmd13)
        self.assertIn("RecordingAlreadyCompletedError", source)

    def test_worker_suppresses_second_ready_event(self):
        source = (COMP / "recording_worker.py").read_text()
        catch = source.index("except RecordingAlreadyCompletedError as err:")
        camera_error = source.index("except CameraStageError as err:", catch)
        duplicate_block = source[catch:camera_error]
        self.assertIn("async_complete_event(event.event_id)", duplicate_block)
        self.assertIn("deduplicated_recordings += 1", duplicate_block)
        self.assertNotIn("RECORDING_READY_EVENT", duplicate_block)
        self.assertIn("completed_recording=CompletedRecording(", source)

    def test_fingerprint_is_non_reversible_hash(self):
        source = (COMP / "recording_probe.py").read_text()
        self.assertIn("def recording_fingerprint", source)
        self.assertIn("hashlib.sha256", source)
        self.assertIn("candidate.record_id", source)
        self.assertIn("candidate.start_time.isoformat", source)

    def test_diagnostics_are_secret_safe(self):
        source = (COMP / "diagnostics.py").read_text()
        self.assertIn('\"completed_recording_count\"', source)
        self.assertIn('\"deduplicated_recordings\"', source)
        self.assertIn('\"last_duplicate_recording_fingerprint_present\"', source)
        self.assertNotIn('\"last_duplicate_recording_fingerprint\":', source)


if __name__ == "__main__":
    unittest.main()
