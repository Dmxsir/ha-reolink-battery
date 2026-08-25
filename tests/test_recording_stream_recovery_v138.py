from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
RECOVERY = COMPONENT / "recording_worker_v138.py"
INIT = COMPONENT / "__init__.py"
PACKAGE = "_reolink_battery_v138_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package


def load_module(name: str):
    full_name = f"{PACKAGE}.{name}"
    spec = importlib.util.spec_from_file_location(full_name, COMPONENT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


load_module("const")
load_module("events")
load_module("recording_worker")
recovery_module = load_module("recording_worker_v138")


def make_worker():
    coordinator = types.SimpleNamespace(deferred_event_count=0)
    runtime = types.SimpleNamespace(coordinator=coordinator)
    entry = types.SimpleNamespace(runtime_data=runtime, entry_id="test-entry")
    hass = types.SimpleNamespace()
    return recovery_module.RecordingWorker(hass, entry)


class RecordingStreamRecoveryV138Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_beta22 = sys.modules.get(
            f"{PACKAGE}.recording_download_beta22"
        )

    def tearDown(self) -> None:
        module_name = f"{PACKAGE}.recording_download_beta22"
        if self._previous_beta22 is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = self._previous_beta22

    def _install_trace(self, **overrides) -> None:
        values = {
            "file_bytes_written": 3_342_336,
            "xml_reported_size": 9_555_011,
            "remote_disconnect_observed": True,
            "termination_reason": "connection_closed",
        }
        values.update(overrides)
        trace = types.SimpleNamespace(**values)
        beta22 = types.ModuleType(f"{PACKAGE}.recording_download_beta22")
        beta22.stream_probe_state = lambda _entry_id: trace
        sys.modules[beta22.__name__] = beta22

    def test_partial_remote_disconnect_is_classified(self) -> None:
        self._install_trace()
        worker = make_worker()

        self.assertTrue(worker._classify_incomplete_stream_failure())
        self.assertEqual(
            worker.state.last_failure_stage,
            recovery_module.INCOMPLETE_STREAM_FAILURE_STAGE,
        )
        self.assertEqual(
            worker.state.last_failure_type,
            "remote_disconnect_before_expected_size",
        )

    def test_partial_idle_timeout_is_classified(self) -> None:
        self._install_trace(
            remote_disconnect_observed=False,
            termination_reason="idle_timeout",
            file_bytes_written=1_261_568,
            xml_reported_size=9_314_178,
        )
        worker = make_worker()

        self.assertTrue(worker._classify_incomplete_stream_failure())
        self.assertEqual(
            worker.state.last_failure_stage,
            recovery_module.INCOMPLETE_STREAM_IDLE_FAILURE_STAGE,
        )
        self.assertEqual(
            worker.state.last_failure_type,
            "idle_timeout_before_expected_size",
        )

    def test_non_partial_or_unrelated_failure_stays_on_base_policy(self) -> None:
        for overrides in (
            {"remote_disconnect_observed": False, "termination_reason": "connection_closed"},
            {"remote_disconnect_observed": False, "termination_reason": "hard_timeout"},
            {"file_bytes_written": 0},
            {"file_bytes_written": 9_555_011},
        ):
            with self.subTest(overrides=overrides):
                self._install_trace(**overrides)
                worker = make_worker()
                self.assertFalse(worker._classify_incomplete_stream_failure())
                self.assertEqual(worker.state.last_failure_stage, "")

    def test_partial_transfer_uses_short_battery_awake_retries(self) -> None:
        worker = make_worker()
        self.assertEqual(
            worker._retry_delay(1, fast_recovery=True, fast_retry_index=0),
            3.0,
        )
        self.assertEqual(
            worker._retry_delay(2, fast_recovery=True, fast_retry_index=1),
            6.0,
        )
        self.assertEqual(
            worker._retry_delay(1, fast_recovery=False, fast_retry_index=0),
            30.0,
        )
        self.assertEqual(
            worker._retry_delay(2, fast_recovery=False, fast_retry_index=0),
            60.0,
        )

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
