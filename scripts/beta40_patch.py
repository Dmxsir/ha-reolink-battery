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
    "    p2p_heartbeat_fresh_tid_enabled: bool = False\n"
    "    p2p_heartbeat_unique_tid_count: int = 0\n"
    "    proactive_cmd234_count: int = 0\n",
    "    p2p_heartbeat_fresh_tid_enabled: bool = False\n"
    "    p2p_heartbeat_unique_tid_count: int = 0\n"
    "    p2p_heartbeat_fresh_tid_activated_after_login: bool = False\n"
    "    p2p_heartbeat_pre_auth_reused_tid_count: int = 0\n"
    "    proactive_cmd234_count: int = 0\n",
    "trace post-auth heartbeat fields",
)

beta20 = replace_once(
    beta20,
    "        self._p2p_heartbeat_started_after_handoff = False\n"
    "        self._p2p_heartbeat_fresh_tid_enabled = True\n"
    "        self._p2p_heartbeat_tids: set[int] = set()\n",
    "        self._p2p_heartbeat_started_after_handoff = False\n"
    "        # Preserve beta.38 heartbeat identity during wake/auth. Fresh IDs\n"
    "        # are enabled explicitly only after Baichuan login succeeds.\n"
    "        self._p2p_heartbeat_fresh_tid_enabled = False\n"
    "        self._p2p_heartbeat_fresh_tid_activated_after_login = False\n"
    "        self._p2p_heartbeat_pre_auth_reused_tid_count = 0\n"
    "        self._p2p_heartbeat_tids: set[int] = set()\n",
    "connection post-auth heartbeat state",
)

old_send = '''        # Use a fresh discovery transaction ID for every C2D_HB instead of
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
'''
new_send = '''        if self._p2p_heartbeat_fresh_tid_enabled:
            transaction_id = secrets.randbelow(999_000) + 1_000
            while (
                transaction_id == self._p2p_heartbeat_tid
                or transaction_id in self._p2p_heartbeat_tids
            ):
                transaction_id = secrets.randbelow(999_000) + 1_000
            self._p2p_heartbeat_tid = transaction_id
            self._p2p_heartbeat_tids.add(transaction_id)
        else:
            # During wake/login keep the exact beta.38 identity behavior. This
            # isolates post-auth lease lifetime from pre-auth compatibility.
            transaction_id = self._p2p_heartbeat_tid
            if transaction_id is None:
                transaction_id = secrets.randbelow(999_000) + 1_000
                self._p2p_heartbeat_tid = transaction_id
            self._p2p_heartbeat_pre_auth_reused_tid_count += 1
        packet = _encode_p2p_heartbeat(
            transaction_id,
            protocol.client_id,
            protocol.host_id,
        )
'''
beta20 = replace_once(beta20, old_send, new_send, "post-auth fresh heartbeat sender")

beta20 = replace_once(
    beta20,
    "    def _record_p2p_heartbeat(self) -> bool:\n",
    "    def activate_fresh_heartbeat_tids_after_login(self) -> None:\n"
    "        \"\"\"Switch heartbeat identity only after authentication succeeds.\"\"\"\n"
    "        self._p2p_heartbeat_fresh_tid_enabled = True\n"
    "        self._p2p_heartbeat_fresh_tid_activated_after_login = True\n\n"
    "    def _record_p2p_heartbeat(self) -> bool:\n",
    "post-auth activation method",
)

beta20 = replace_once(
    beta20,
    "        trace.p2p_heartbeat_unique_tid_count = len(\n"
    "            getattr(self, \"_p2p_heartbeat_tids\", set())\n"
    "        )\n"
    "        if snapshot_pre_cmd13:\n",
    "        trace.p2p_heartbeat_unique_tid_count = len(\n"
    "            getattr(self, \"_p2p_heartbeat_tids\", set())\n"
    "        )\n"
    "        trace.p2p_heartbeat_fresh_tid_activated_after_login = bool(\n"
    "            getattr(self, \"_p2p_heartbeat_fresh_tid_activated_after_login\", False)\n"
    "        )\n"
    "        trace.p2p_heartbeat_pre_auth_reused_tid_count = int(\n"
    "            getattr(self, \"_p2p_heartbeat_pre_auth_reused_tid_count\", 0)\n"
    "        )\n"
    "        if snapshot_pre_cmd13:\n",
    "post-auth heartbeat trace copy",
)
beta20_path.write_text(beta20)


base_path = root / "custom_components/reolink_battery/recording_download_probe.py"
base = base_path.read_text()
base = replace_once(
    base,
    "        host.baichuan._first_login = False\n"
    "        await host.baichuan.login()\n\n"
    "        event_time = event.notification_post_time or event.alarm_time\n",
    "        host.baichuan._first_login = False\n"
    "        await host.baichuan.login()\n"
    "        activate_fresh_heartbeat = getattr(\n"
    "            connection, \"activate_fresh_heartbeat_tids_after_login\", None\n"
    "        )\n"
    "        if callable(activate_fresh_heartbeat):\n"
    "            activate_fresh_heartbeat()\n\n"
    "        event_time = event.notification_post_time or event.alarm_time\n",
    "activate fresh heartbeat after login",
)
base_path.write_text(base)


diagnostics_path = root / "custom_components/reolink_battery/diagnostics.py"
diagnostics = diagnostics_path.read_text()
diagnostics = replace_once(
    diagnostics,
    "                \"p2p_heartbeat_unique_tid_count\": (\n"
    "                    stream.p2p_heartbeat_unique_tid_count\n"
    "                ),\n"
    "                \"proactive_cmd234_count\": stream.proactive_cmd234_count,\n",
    "                \"p2p_heartbeat_unique_tid_count\": (\n"
    "                    stream.p2p_heartbeat_unique_tid_count\n"
    "                ),\n"
    "                \"p2p_heartbeat_fresh_tid_activated_after_login\": (\n"
    "                    stream.p2p_heartbeat_fresh_tid_activated_after_login\n"
    "                ),\n"
    "                \"p2p_heartbeat_pre_auth_reused_tid_count\": (\n"
    "                    stream.p2p_heartbeat_pre_auth_reused_tid_count\n"
    "                ),\n"
    "                \"proactive_cmd234_count\": stream.proactive_cmd234_count,\n",
    "post-auth heartbeat diagnostics",
)
diagnostics = replace_once(
    diagnostics,
    '        "milestone": "3B.13-fresh-p2p-heartbeat-tid",\n',
    '        "milestone": "3B.14-post-auth-fresh-heartbeat-tid",\n',
    "milestone",
)
diagnostics_path.write_text(diagnostics)


manifest_path = root / "custom_components/reolink_battery/manifest.json"
manifest = manifest_path.read_text()
manifest = replace_once(
    manifest,
    '"version": "0.1.2-beta.39"',
    '"version": "0.1.2-beta.40"',
    "manifest version",
)
manifest_path.write_text(manifest)


test_path = root / "tests/test_recording_download_beta40.py"
test_path.write_text(
    '''"""Regression guards for beta.40 post-auth fresh heartbeat IDs."""\n\nfrom pathlib import Path\nimport unittest\n\nROOT = Path(__file__).parents[1]\nBETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"\nBASE = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe.py"\nBETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"\nWORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"\nDIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"\nMANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"\n\n\nclass Beta40PostAuthFreshHeartbeatTests(unittest.TestCase):\n    def test_wake_auth_preserves_beta38_heartbeat_identity(self):\n        source = BETA20.read_text()\n        self.assertIn("self._p2p_heartbeat_fresh_tid_enabled = False", source)\n        self.assertIn("self._p2p_heartbeat_pre_auth_reused_tid_count += 1", source)\n        self.assertIn("transaction_id = self._p2p_heartbeat_tid", source)\n\n    def test_fresh_ids_activate_only_after_login_success(self):\n        source = BETA20.read_text()\n        base = BASE.read_text()\n        self.assertIn("def activate_fresh_heartbeat_tids_after_login", source)\n        self.assertIn("self._p2p_heartbeat_fresh_tid_enabled = True", source)\n        login_pos = base.index("await host.baichuan.login()")\n        activate_pos = base.index("activate_fresh_heartbeat_tids_after_login")\n        self.assertGreater(activate_pos, login_pos)\n\n    def test_beta38_and_beta39_post_auth_transport_contracts_are_preserved(self):\n        source = BETA20.read_text()\n        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)\n        self.assertIn("self.udp_immediate_ack_suppressed_count += 1", source)\n        self.assertIn("range(self._recv_seq_id + 1, highest + 1)", source)\n        self.assertIn("self._p2p_heartbeat_tids.add(transaction_id)", source)\n        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)\n        self.assertIn("RELIABLE_UDP_ACK_TIMEOUT = 0.5", source)\n        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)\n        beta21 = BETA21.read_text()\n        self.assertEqual(beta21.count("_send_reliable_download_packet("), 2)\n        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", WORKER.read_text())\n\n    def test_diagnostics_and_version(self):\n        diagnostics = DIAGNOSTICS.read_text()\n        for token in (\n            "p2p_heartbeat_fresh_tid_activated_after_login",\n            "p2p_heartbeat_pre_auth_reused_tid_count",\n            "3B.14-post-auth-fresh-heartbeat-tid",\n        ):\n            self.assertIn(token, diagnostics)\n        self.assertIn('"version": "0.1.2-beta.40"', MANIFEST.read_text())\n\n\nif __name__ == "__main__":\n    unittest.main()\n'''
)
