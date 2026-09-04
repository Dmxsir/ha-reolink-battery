"""Deterministic receive/reorder/ACK tests for the production UDP protocol."""

from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
import threading
import types
import unittest
from enum import IntFlag
from pathlib import Path

from reolink_aio.baichuan.udp_protocol import MAGIC_UDP_ACK, MAGIC_UDP_BC

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_udp_reliability_test"


def _install_home_assistant_import_stubs() -> None:
    """Provide only the two HA types imported while loading the protocol."""
    if "homeassistant.components.camera" in sys.modules:
        return

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    camera = types.ModuleType("homeassistant.components.camera")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    entity = types.ModuleType("homeassistant.helpers.entity")

    class Camera:
        def __init__(self) -> None:
            pass

    class CameraEntityFeature(IntFlag):
        STREAM = 1

    class DeviceInfo(dict):
        pass

    camera.Camera = Camera
    camera.CameraEntityFeature = CameraEntityFeature
    entity.DeviceInfo = DeviceInfo
    core.HomeAssistant = object
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.camera": camera,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.entity": entity,
        }
    )


_install_home_assistant_import_stubs()
package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
package.ReolinkBatteryConfigEntry = object
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta20")
verified = importlib.import_module(f"{PACKAGE}.recording_download_beta22")
diagnostics = importlib.import_module(f"{PACKAGE}.diagnostics")

CLIENT_ID = 0x10203040
HOST_ID = 0x50607080
REMOTE = ("192.0.2.1", 29999)


class _RecordingTransport:
    def __init__(self) -> None:
        self.datagrams: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.datagrams.append((data, addr))


class _PayloadRecordingProtocol(probe._P2PHeartbeatProbeProtocol):
    """Production protocol with application parsing replaced by an order sink."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        async def _drop() -> None:
            return None

        self._test_drop_coroutine = _drop()
        super().__init__(
            loop,
            REMOTE[0],
            self._test_drop_coroutine,
            lambda _seq_id: None,
            None,
            None,
        )
        self.client_id = CLIENT_ID
        self.host_id = HOST_ID
        self.remote_port = REMOTE[1]
        self.delivered: list[int] = []

    def bc_data_received(self, data: bytes) -> None:
        self.delivered.append(int.from_bytes(data, "little"))

    def finish_test(self) -> None:
        self._test_drop_coroutine.close()


def _bc_packet(seq_id: int) -> bytes:
    payload = seq_id.to_bytes(4, "little")
    return (
        bytes.fromhex(MAGIC_UDP_BC)
        + CLIENT_ID.to_bytes(4, "little")
        + bytes(4)
        + seq_id.to_bytes(4, "little")
        + len(payload).to_bytes(4, "little")
        + payload
    )


def _ack_state(datagram: bytes) -> tuple[int, bytes]:
    if datagram[:4] != bytes.fromhex(MAGIC_UDP_ACK):
        raise AssertionError("not a UDP ACK")
    payload_size = int.from_bytes(datagram[24:28], "little")
    return int.from_bytes(datagram[16:20], "little"), datagram[28 : 28 + payload_size]


class ProductionReceiveHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.protocol = _PayloadRecordingProtocol(asyncio.get_running_loop())
        self.transport = _RecordingTransport()
        self.protocol.connection_made(self.transport)
        # Model a live session where sequence zero was already consumed.
        self.protocol._recv_seq_id = 0

    async def asyncTearDown(self) -> None:
        self.protocol.finish_test()

    def receive(self, *seq_ids: int) -> None:
        for seq_id in seq_ids:
            self.protocol.datagram_received(_bc_packet(seq_id), REMOTE)

    def ack(self) -> tuple[int, bytes]:
        self.assertTrue(self.protocol.send_periodic_ack())
        return _ack_state(self.transport.datagrams[-1][0])

    async def test_in_order_packets_advance_contiguously(self) -> None:
        self.receive(1, 2, 3, 4, 5)
        self.assertEqual(self.protocol.delivered, [1, 2, 3, 4, 5])
        self.assertEqual(self.transport.datagrams, [])
        self.assertEqual(self.ack(), (5, b""))
        self.assertEqual(self.protocol.udp_seq_gap_events, 0)

    async def test_gap_buffers_future_packets_and_recovers_through_them(self) -> None:
        transitions = []
        for seq_id in (1, 2, 4, 5, 3):
            self.receive(seq_id)
            transitions.append(self.ack())

        self.assertEqual(
            transitions,
            [(1, b""), (2, b""), (2, b"\x00\x01"), (2, b"\x00\x01\x01"), (5, b"")],
        )
        self.assertEqual(self.protocol.delivered, [1, 2, 3, 4, 5])
        self.assertEqual(self.protocol.udp_recovered_missing_packet_count, 1)

    async def test_duplicate_out_of_order_packet_does_not_corrupt_window(self) -> None:
        self.receive(1, 2, 4, 4, 5, 3)
        self.assertEqual(self.protocol.delivered, [1, 2, 3, 4, 5])
        self.assertEqual(self.ack(), (5, b""))
        self.assertEqual(self.protocol.udp_duplicate_packets, 1)
        self.assertEqual(self.protocol.udp_missing_packet_count, 1)

    async def test_multiple_gaps_keep_only_truly_missing_sequences(self) -> None:
        self.receive(1, 3, 5)
        self.assertEqual(self.ack(), (1, b"\x00\x01\x00\x01"))
        self.receive(2, 4, 6)
        self.assertEqual(self.protocol.delivered, [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.ack(), (6, b""))
        self.assertEqual(self.protocol.udp_missing_packet_count, 2)
        self.assertEqual(self.protocol.udp_recovered_missing_packet_count, 2)

    async def test_long_reordering_window_drains_without_loss(self) -> None:
        self.receive(1, *range(3, 515), 2)
        self.assertEqual(self.protocol.delivered, list(range(1, 515)))
        self.assertEqual(self.ack(), (514, b""))

    async def test_duplicate_retransmissions_after_recovery_are_ignored(self) -> None:
        self.receive(1, 3, 2, 2, 3, 4)
        self.assertEqual(self.protocol.delivered, [1, 2, 3, 4])
        self.assertEqual(self.protocol.udp_duplicate_packets, 2)
        self.assertEqual(self.ack(), (4, b""))

    async def test_large_stream_with_bounded_reordering_is_exact(self) -> None:
        self.receive(1)
        for seq_id in range(2, 10_001, 2):
            self.receive(seq_id + 1, seq_id)
        self.assertEqual(self.protocol.delivered, list(range(1, 10_002)))
        self.assertEqual(self.ack(), (10_001, b""))
        self.assertLessEqual(self.protocol.udp_reorder_buffer_peak, 1)

    async def test_fragmented_datagram_is_not_delivered_until_complete(self) -> None:
        packet = _bc_packet(1)
        self.protocol.datagram_received(packet[:21], REMOTE)
        self.assertEqual(self.protocol.delivered, [])
        self.protocol.datagram_received(packet[21:], REMOTE)
        self.assertEqual(self.protocol.delivered, [1])

    async def test_current_gap_snapshot_is_captured_at_transfer_end(self) -> None:
        self.protocol._stream_started = True
        self.receive(1, 3, 5)
        connection = object.__new__(probe._P2PHeartbeatFullTransferConnection)
        connection._loop = asyncio.get_running_loop()
        connection._protocol = self.protocol
        connection._reliable_command_seq = {}
        connection._reliable_acked_seq_ids = set()
        connection._reliable_ack_delays_ms = {}
        connection._reliable_command_retransmits = {}
        connection._udp_socket_receive_buffer_configured_bytes = None
        connection._udp_socket_receive_buffer_effective_bytes = 212_992
        trace = probe._new_trace(attempted=True)

        connection._apply_udp_reliability_trace(
            trace, protocol=self.protocol
        )

        self.assertEqual(trace.udp_current_unrecovered_missing_packet_count, 2)
        self.assertEqual(trace.udp_current_buffered_out_of_order, 2)
        self.assertEqual(trace.udp_current_highest_buffered_seq, 5)
        self.assertEqual(trace.udp_current_expected_next_seq, 2)
        self.assertEqual(trace.udp_socket_receive_buffer_effective_bytes, 212_992)

    async def test_idle_requires_no_recent_media_or_recovery_activity(self) -> None:
        self.protocol._stream_started = True
        self.receive(1, 3)
        self.assertIn(3, self.protocol._seq_data)
        last_activity = self.protocol._udp_last_media_datagram_at
        self.assertFalse(
            self.protocol.stream_idle_expired(
                last_activity + probe.STREAM_IDLE_TIMEOUT - 0.001,
                probe.STREAM_IDLE_TIMEOUT,
            )
        )
        self.assertTrue(
            self.protocol.stream_idle_expired(
                last_activity + probe.STREAM_IDLE_TIMEOUT,
                probe.STREAM_IDLE_TIMEOUT,
            )
        )
        self.protocol._udp_last_gap_recovery_at = last_activity + 29.0
        self.assertFalse(
            self.protocol.stream_idle_expired(
                last_activity + 31.0,
                probe.STREAM_IDLE_TIMEOUT,
            )
        )

    async def test_32_bit_boundary_and_fresh_session_reset(self) -> None:
        self.protocol._recv_seq_id = 0xFFFFFFFE
        self.receive(0xFFFFFFFF)
        self.assertEqual(self.protocol.delivered, [0xFFFFFFFF])
        self.assertEqual(self.ack(), (0xFFFFFFFF, b""))

        fresh = _PayloadRecordingProtocol(asyncio.get_running_loop())
        fresh_transport = _RecordingTransport()
        fresh.connection_made(fresh_transport)
        try:
            fresh.datagram_received(_bc_packet(0), REMOTE)
            self.assertEqual(fresh.delivered, [0])
            self.assertEqual(fresh._recv_seq_id, 0)
        finally:
            fresh.finish_test()

    async def test_probe_cleanup_cancels_all_pending_futures(self) -> None:
        trace = probe._new_trace(attempted=True)
        first = self.protocol.arm_stream_probe(7, trace, lambda _frame: None)
        stop = self.protocol._stream_stop_future
        cmd8 = self.protocol.arm_cmd8_delivery_future()
        file_probe = self.protocol.arm_file_download_probe(8)

        self.protocol.clear_stream_probe()
        self.protocol.clear_file_download_probe()

        self.assertTrue(first.cancelled())
        self.assertIsNotNone(stop)
        self.assertTrue(stop.cancelled())
        self.assertTrue(cmd8.cancelled())
        self.assertTrue(file_probe.cancelled())

    async def test_connection_cleanup_cancels_reliable_ack_waiters(self) -> None:
        connection = object.__new__(probe._P2PHeartbeatFullTransferConnection)
        pending = asyncio.get_running_loop().create_future()
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(True)
        connection._reliable_ack_waiters = {1: pending, 2: completed}

        cancelled = connection._cancel_reliable_ack_waiters()

        self.assertEqual(cancelled, 1)
        self.assertTrue(pending.cancelled())
        self.assertTrue(completed.done())
        self.assertEqual(connection._reliable_ack_waiters, {})


class VerifiedPersistenceHarness(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def connection(part: Path, final: Path, data: bytes, expected: int):
        connection = object.__new__(verified._VerifiedFileConnection)
        connection._stream_trace = verified._new_trace(attempted=True)
        connection._stream_trace.mp4_offset = 0
        connection._stream_trace.xml_reported_size = expected
        connection._stream_trace.expected_size_match = len(data) == expected
        connection._part_path = part
        connection._final_path = final
        connection._aggregate = bytearray(data)
        return connection

    async def test_verified_file_is_fsynced_and_atomically_published(self) -> None:
        data = b"\x00\x00\x00\x18ftypisom" + bytes(52)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            part = root / "recording.mp4.part"
            final = root / "recording.mp4"
            connection = self.connection(part, final, data, len(data))

            await connection._async_finalize_collected_file()

            self.assertEqual(final.read_bytes(), data)
            self.assertFalse(part.exists())
            self.assertTrue(connection._stream_trace.fsync_completed)
            self.assertTrue(connection._stream_trace.atomic_rename_completed)
            self.assertTrue(connection._stream_trace.file_saved)

    async def test_verified_subclass_retains_collector_until_finalization(self) -> None:
        connection = object.__new__(verified._VerifiedFileConnection)
        connection._aggregate = bytearray(b"complete-media")

        connection._release_collected_media()

        self.assertEqual(connection._aggregate, bytearray(b"complete-media"))

    async def test_incomplete_file_is_never_published(self) -> None:
        data = b"\x00\x00\x00\x18ftypisom" + bytes(20)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            part = root / "recording.mp4.part"
            final = root / "recording.mp4"
            connection = self.connection(part, final, data, len(data) + 1)

            await connection._async_finalize_collected_file()

            self.assertFalse(final.exists())
            self.assertFalse(part.exists())
            self.assertTrue(connection._stream_trace.part_removed_on_failure)
            self.assertFalse(connection._stream_trace.file_saved)

    async def test_file_finalization_runs_outside_the_event_loop_thread(self) -> None:
        event_loop_thread = threading.get_ident()

        class _ThreadCheckingConnection(verified._VerifiedFileConnection):
            def _finalize_verified_file(self, data: memoryview) -> None:
                self.finalizer_thread = threading.get_ident()

        connection = object.__new__(_ThreadCheckingConnection)
        connection._stream_trace = verified._new_trace(attempted=True)
        connection._stream_trace.mp4_offset = 0
        connection._aggregate = bytearray(b"data")

        await connection._async_finalize_collected_file()

        self.assertNotEqual(connection.finalizer_thread, event_loop_thread)


class DiagnosticsContractTests(unittest.TestCase):
    def test_completion_ratio_uses_collected_and_expected_bytes(self) -> None:
        self.assertEqual(diagnostics._completion_ratio(2_800_000, 10_300_000), 0.2718)
        self.assertIsNone(diagnostics._completion_ratio(100, None))

    def test_schema_does_not_claim_an_obsolete_integration_version(self) -> None:
        source = (COMPONENT / "diagnostics.py").read_text()
        self.assertNotIn("1.3.1-live-view-diagnostics", source)
        self.assertIn('"diagnostics_schema": "transport-reliability-v1"', source)
        self.assertIn('"counter_lifetime": "persistent_storage"', source)
        self.assertIn(
            '"counter_lifetime": "config_entry_runtime_since_setup"', source
        )


if __name__ == "__main__":
    unittest.main()
