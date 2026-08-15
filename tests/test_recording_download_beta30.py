"""Offline beta.30 tests for single UID/LAN lease handoff."""

from __future__ import annotations

import importlib
import socket
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_beta30_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
beta20 = importlib.import_module(f"{PACKAGE}.recording_download_probe_beta20")
transport_mod = beta20.beta17.transport_mod


class LeaseOwnershipTests(unittest.TestCase):
    def test_detach_transfers_socket_without_disconnect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        lease = transport_mod.UidLanLease(
            "127.0.0.1", 29999, "127.0.0.1", 1, 111, 222, 333, sock
        )
        taken = lease.detach_socket()
        self.assertIs(taken, sock)
        self.assertIsNone(lease.socket)
        lease.close()
        self.assertGreaterEqual(taken.fileno(), 0)
        taken.close()


class SingleLeaseConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handoff_reuses_socket_ids_port_and_transaction_id(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        local_port = sock.getsockname()[1]
        lease = transport_mod.UidLanLease(
            "127.0.0.1", 29998, "127.0.0.1", 1, 444, 555, 666, sock
        )
        connection = beta20._P2PHeartbeatFullTransferConnection(
            "127.0.0.1",
            "127.0.0.1",
            0,
            None,
            None,
            uid="ABC123",
            handoff_lease=lease,
        )
        await connection.connect()
        try:
            self.assertTrue(connection.connection_open)
            self.assertTrue(connection._handoff_mode)
            self.assertTrue(connection._handoff_active)
            self.assertIsNone(lease.socket)
            self.assertEqual(connection._local_port, local_port)
            self.assertEqual(connection._port, 29998)
            self.assertEqual(connection._protocol.client_id, 444)
            self.assertEqual(connection._protocol.host_id, 555)
            self.assertEqual(connection._protocol.remote_port, 29998)
            self.assertEqual(connection._handoff_transaction_id, 666)
            self.assertEqual(connection._p2p_heartbeat_tid, 666)
        finally:
            await transport_mod.BaichuanBaseConnection.close(connection)

    async def test_consumed_handoff_never_falls_back_to_second_connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        lease = transport_mod.UidLanLease(
            "127.0.0.1", 29997, "127.0.0.1", 1, 777, 888, 999, sock
        )
        connection = beta20._P2PHeartbeatFullTransferConnection(
            "127.0.0.1", "127.0.0.1", 0, None, None, uid="ABC123", handoff_lease=lease
        )
        await connection.connect()
        await transport_mod.BaichuanBaseConnection.close(connection)
        with self.assertRaises(transport_mod.ReolinkConnectionError):
            await connection.connect()


if __name__ == "__main__":
    unittest.main()
