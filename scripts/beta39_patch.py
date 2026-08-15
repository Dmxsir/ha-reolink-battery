from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


root = Path(__file__).parents[1]
beta20_path = root / "custom_components/reolink_battery/recording_download_probe_beta20.py"
beta20 = beta20_path.read_text()

beta20 = replace_once(
    beta20,
    "    p2p_heartbeat_background_task_active: bool = False\n"
    "    proactive_cmd234_count: int = 0\n",
    "    p2p_heartbeat_background_task_active: bool = False\n"
    "    p2p_heartbeat_fresh_tid_enabled: bool = False\n"
    "    p2p_heartbeat_unique_tid_count: int = 0\n"
    "    proactive_cmd234_count: int = 0\n",
    "trace heartbeat tid fields",
)

beta20 = replace_once(
    beta20,
    "        self._p2p_heartbeat_total_count = 0\n"
    "        self._p2p_heartbeat_started_after_handoff = False\n",
    "        self._p2p_heartbeat_total_count = 0\n"
    "        self._p2p_heartbeat_started_after_handoff = False\n"
    "        self._p2p_heartbeat_fresh_tid_enabled = True\n"
    "        self._p2p_heartbeat_tids: set[int] = set()\n",
    "connection heartbeat tid state",
)

old_send = """        transaction_id = self._p2p_heartbeat_tid
        if transaction_id is None:
            transaction_id = secrets.randbelow(999_000) + 1_000
            self._p2p_heartbeat_tid = transaction_id
        packet = _encode_p2p_heartbeat(
            transaction_id,
            protocol.client_id,
            protocol.host_id,
        )
        transport.sendto(packet, (self._host, self._port))
        return True
"""
new_send = """        # Use a fresh discovery transaction ID for every C2D_HB instead of
        # replaying the original C2D_C transaction ID for the whole session.
        transaction_id = secrets.randbelow(999_000) + 1_000
        while (
            transaction_id == self._p2p_heartbeat_tid
            or transaction_id in self._p2p_heartbeat_tids
        ):
            transaction_id = secrets.randbelow(999_000) + 1_000
        self._p2p_heartbeat_tid = transaction_id
        self._p2p_heartbeat_tids.add(transaction_id)
        packet = _encode_p2p_heartbeat(
            transaction_id,
            protocol.client_id,
            protocol.host_id,
        )
        transport.sendto(packet, (self._host, self._port))
        return True
"""
beta20 = replace_once(beta20, old_send, new_send, "fresh heartbeat transaction ID")

beta20 = replace_once(
    beta20,
    "        trace.p2p_heartbeat_background_task_active = bool(\n"
    "            task is not None and not task.done()\n"
    "        )\n"
    "        if snapshot_pre_cmd13:\n",
    "        trace.p2p_heartbeat_background_task_active = bool(\n"
    "            task is not None and not task.done()\n"
    "        )\n"
    "        trace.p2p_heartbeat_fresh_tid_enabled = (\n"
    "            self._p2p_heartbeat_fresh_tid_enabled\n"
    "        )\n"
    "        trace.p2p_heartbeat_unique_tid_count = len(\n"
    "            self._p2p_heartbeat_tids\n"
    "        )\n"
    "        if snapshot_pre_cmd13:\n",
    "heartbeat tid trace copy",
)
beta20_path.write_text(beta20)


diagnostics_path = root / "custom_components/reolink_battery/diagnostics.py"
diagnostics = diagnostics_path.read_text()
diagnostics = replace_once(
    diagnostics,
    "                \"p2p_heartbeat_background_task_active\": (\n"
    "                    stream.p2p_heartbeat_background_task_active\n"
    "                ),\n"
    "                \"proactive_cmd234_count\": stream.proactive_cmd234_count,\n",
    "                \"p2p_heartbeat_background_task_active\": (\n"
    "                    stream.p2p_heartbeat_background_task_active\n"
    "                ),\n"
    "                \"p2p_heartbeat_fresh_tid_enabled\": (\n"
    "                    stream.p2p_heartbeat_fresh_tid_enabled\n"
    "                ),\n"
    "                \"p2p_heartbeat_unique_tid_count\": (\n"
    "                    stream.p2p_heartbeat_unique_tid_count\n"
    "                ),\n"
    "                \"proactive_cmd234_count\": stream.proactive_cmd234_count,\n",
    "heartbeat tid diagnostics",
)
diagnostics = replace_once(
    diagnostics,
    '        "milestone": "3B.12-periodic-only-rx-ack",\n',
    '        "milestone": "3B.13-fresh-p2p-heartbeat-tid",\n',
    "milestone",
)
diagnostics_path.write_text(diagnostics)


manifest_path = root / "custom_components/reolink_battery/manifest.json"
manifest = manifest_path.read_text()
manifest = replace_once(
    manifest,
    '"version": "0.1.2-beta.38"',
    '"version": "0.1.2-beta.39"',
    "manifest version",
)
manifest_path.write_text(manifest)


test_path = root / "tests/test_recording_download_beta39.py"
test_path.write_text(
    '''"""Regression guards for beta.39 fresh P2P heartbeat transaction IDs."""\n\nfrom pathlib import Path\nimport unittest\n\nROOT = Path(__file__).parents[1]\nBETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"\nBETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"\nWORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"\nDIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"\nMANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"\n\n\nclass Beta39FreshHeartbeatTidTests(unittest.TestCase):\n    def test_every_heartbeat_gets_a_fresh_transaction_id(self):\n        source = BETA20.read_text()\n        self.assertIn("self._p2p_heartbeat_tids: set[int] = set()", source)\n        self.assertIn("transaction_id = secrets.randbelow(999_000) + 1_000", source)\n        self.assertIn("transaction_id == self._p2p_heartbeat_tid", source)\n        self.assertIn("transaction_id in self._p2p_heartbeat_tids", source)\n        self.assertIn("self._p2p_heartbeat_tids.add(transaction_id)", source)\n        self.assertIn("p2p_heartbeat_unique_tid_count", source)\n\n    def test_beta38_periodic_only_ack_contract_is_preserved(self):\n        source = BETA20.read_text()\n        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)\n        self.assertIn("def _send_ack_now(self) -> bool:", source)\n        self.assertIn("self.udp_immediate_ack_suppressed_count += 1", source)\n        self.assertIn("range(self._recv_seq_id + 1, highest + 1)", source)\n\n    def test_existing_download_transport_is_unchanged(self):\n        source = BETA20.read_text()\n        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)\n        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)\n        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)\n        beta21 = BETA21.read_text()\n        self.assertEqual(beta21.count("_send_reliable_download_packet("), 2)\n        self.assertIn("protocol=protocol", beta21)\n        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", WORKER.read_text())\n\n    def test_diagnostics_and_version(self):\n        diagnostics = DIAGNOSTICS.read_text()\n        for token in (\n            "p2p_heartbeat_fresh_tid_enabled",\n            "p2p_heartbeat_unique_tid_count",\n            "3B.13-fresh-p2p-heartbeat-tid",\n        ):\n            self.assertIn(token, diagnostics)\n        self.assertIn('"version": "0.1.2-beta.39"', MANIFEST.read_text())\n\n\nif __name__ == "__main__":\n    unittest.main()\n'''
)
