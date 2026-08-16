from pathlib import Path

ROOT = Path(__file__).parents[1]
TRANSPORT = ROOT / "custom_components/reolink_battery/transport.py"
BASE = ROOT / "custom_components/reolink_battery/recording_download_probe.py"
WORKER = ROOT / "custom_components/reolink_battery/recording_worker.py"
DIAGNOSTICS = ROOT / "custom_components/reolink_battery/diagnostics.py"
MANIFEST = ROOT / "custom_components/reolink_battery/manifest.json"
BETA40_TEST = ROOT / "tests/test_recording_download_beta40.py"
BETA41_TEST = ROOT / "tests/test_recording_download_beta41.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


# transport.py: extend only UID/LAN discovery timeout and add secret-safe trace.
text = TRANSPORT.read_text()
text = replace_once(
    text,
    'FILE_DOWNLOAD_MESSAGE_CLASS = 0x6482\nFILE_DOWNLOAD_CLASS_WIRE = FILE_DOWNLOAD_MESSAGE_CLASS.to_bytes(2, "little")\nMODERN_24_CLASS_WIRE = (0x6414).to_bytes(2, "little")\n',
    'FILE_DOWNLOAD_MESSAGE_CLASS = 0x6482\nFILE_DOWNLOAD_CLASS_WIRE = FILE_DOWNLOAD_MESSAGE_CLASS.to_bytes(2, "little")\nMODERN_24_CLASS_WIRE = (0x6414).to_bytes(2, "little")\nUID_RESOLVE_TIMEOUT_SECONDS = 15.0\nUID_RESOLVE_RESEND_SECONDS = 0.5\n',
    "transport constants",
)
text = replace_once(
    text,
    '@dataclass(slots=True)\nclass UidLanLease:\n',
    '@dataclass(slots=True)\nclass UidResolveTrace:\n    """Secret-safe timing/cadence telemetry for one UID LAN wake attempt."""\n\n    timeout_seconds: float = UID_RESOLVE_TIMEOUT_SECONDS\n    resend_interval_seconds: float = UID_RESOLVE_RESEND_SECONDS\n    send_rounds: int = 0\n    datagrams_sent: int = 0\n    elapsed_ms: float | None = None\n    succeeded: bool = False\n\n\n@dataclass(slots=True)\nclass UidLanLease:\n',
    "uid trace dataclass",
)
text = replace_once(
    text,
    'def resolve_uid_lan(\n    uid: str,\n    interface: ipaddress.IPv4Interface,\n    timeout: float = 10.0,\n) -> UidLanLease:\n',
    'def resolve_uid_lan(\n    uid: str,\n    interface: ipaddress.IPv4Interface,\n    timeout: float = UID_RESOLVE_TIMEOUT_SECONDS,\n    trace: UidResolveTrace | None = None,\n) -> UidLanLease:\n',
    "resolve signature",
)
text = replace_once(
    text,
    '    if timeout <= 0:\n        raise ValueError("timeout must be positive")\n    _, interface_index = linux_ipv4_interface(str(interface.ip))\n',
    '    if timeout <= 0:\n        raise ValueError("timeout must be positive")\n    started_at = time.monotonic()\n    if trace is not None:\n        trace.timeout_seconds = float(timeout)\n        trace.resend_interval_seconds = UID_RESOLVE_RESEND_SECONDS\n        trace.send_rounds = 0\n        trace.datagrams_sent = 0\n        trace.elapsed_ms = None\n        trace.succeeded = False\n    _, interface_index = linux_ipv4_interface(str(interface.ip))\n',
    "resolve trace init",
)
text = replace_once(
    text,
    '            if now >= next_send:\n                for target in targets:\n                    sock.sendto(packet, target)\n                next_send = now + 0.5\n',
    '            if now >= next_send:\n                for target in targets:\n                    sock.sendto(packet, target)\n                if trace is not None:\n                    trace.send_rounds += 1\n                    trace.datagrams_sent += len(targets)\n                next_send = now + UID_RESOLVE_RESEND_SECONDS\n',
    "resolve resend accounting",
)
text = replace_once(
    text,
    '            device_id = _parse_reply(data, client_id)\n            if device_id is not None:\n                return UidLanLease(\n',
    '            device_id = _parse_reply(data, client_id)\n            if device_id is not None:\n                if trace is not None:\n                    trace.elapsed_ms = round((time.monotonic() - started_at) * 1000.0, 3)\n                    trace.succeeded = True\n                return UidLanLease(\n',
    "resolve success trace",
)
text = replace_once(
    text,
    '    except BaseException:\n        sock.close()\n        raise\n    sock.close()\n    raise TimeoutError(f"UID LAN resolution timed out after {timeout:.1f} seconds")\n',
    '    except BaseException:\n        if trace is not None and trace.elapsed_ms is None:\n            trace.elapsed_ms = round((time.monotonic() - started_at) * 1000.0, 3)\n        sock.close()\n        raise\n    if trace is not None:\n        trace.elapsed_ms = round((time.monotonic() - started_at) * 1000.0, 3)\n    sock.close()\n    raise TimeoutError(f"UID LAN resolution timed out after {timeout:.1f} seconds")\n',
    "resolve failure trace",
)
TRANSPORT.write_text(text)


# recording_download_probe.py: carry wake telemetry through success/failure without changing protocol.
text = BASE.read_text()
text = replace_once(
    text,
    '    FILE_DOWNLOAD_MESSAGE_CLASS,\n    BoundBaichuanUdpConnection,\n    FileDownloadFrameMetadata,\n',
    '    FILE_DOWNLOAD_MESSAGE_CLASS,\n    UID_RESOLVE_TIMEOUT_SECONDS,\n    BoundBaichuanUdpConnection,\n    FileDownloadFrameMetadata,\n    UidResolveTrace,\n',
    "base transport imports",
)
text = replace_once(
    text,
    '        file_info_trace: FileInfoTrace | None = None,\n    ) -> None:\n        super().__init__(stage)\n        self.failure_type = failure_type\n        self.response_code = response_code\n        self.file_info_trace = file_info_trace\n',
    '        file_info_trace: FileInfoTrace | None = None,\n        uid_resolve_trace: UidResolveTrace | None = None,\n    ) -> None:\n        super().__init__(stage)\n        self.failure_type = failure_type\n        self.response_code = response_code\n        self.file_info_trace = file_info_trace\n        self.uid_resolve_trace = uid_resolve_trace\n',
    "DownloadPrepareError trace",
)
text = replace_once(
    text,
    '    response_accepted: bool\n    file_info_trace: FileInfoTrace\n    identity_trace: RecordingIdentityTrace\n',
    '    response_accepted: bool\n    file_info_trace: FileInfoTrace\n    identity_trace: RecordingIdentityTrace\n    uid_resolve_trace: UidResolveTrace\n',
    "DownloadPrepareResult trace",
)
text = replace_once(
    text,
    '    resolve_timeout: float = 10.0,\n    command_timeout: int = 30,\n',
    '    resolve_timeout: float = UID_RESOLVE_TIMEOUT_SECONDS,\n    command_timeout: int = 30,\n',
    "base resolve timeout",
)
text = replace_once(
    text,
    '    file_info_trace = FileInfoTrace()\n    try:\n',
    '    file_info_trace = FileInfoTrace()\n    uid_resolve_trace = UidResolveTrace(timeout_seconds=float(resolve_timeout))\n    try:\n',
    "base uid trace init",
)
text = replace_once(
    text,
    '        lease = await asyncio.to_thread(\n            resolve_uid_lan, uid, interface, resolve_timeout\n        )\n',
    '        lease = await asyncio.to_thread(\n            resolve_uid_lan, uid, interface, resolve_timeout, uid_resolve_trace\n        )\n',
    "base resolve call",
)
text = replace_once(
    text,
    '            file_info_trace=file_info_trace,\n            identity_trace=identity_trace,\n        )\n    except CameraStageError:\n        raise\n',
    '            file_info_trace=file_info_trace,\n            identity_trace=identity_trace,\n            uid_resolve_trace=uid_resolve_trace,\n        )\n    except CameraStageError as err:\n        if isinstance(err, DownloadPrepareError) and err.uid_resolve_trace is None:\n            err.uid_resolve_trace = uid_resolve_trace\n        raise\n',
    "base result/error trace",
)
text = replace_once(
    text,
    '            response_code=rsp_code,\n            file_info_trace=file_info_trace,\n        ) from None\n',
    '            response_code=rsp_code,\n            file_info_trace=file_info_trace,\n            uid_resolve_trace=uid_resolve_trace,\n        ) from None\n',
    "base outer error trace",
)
BASE.write_text(text)


# recording_worker.py: persist only secret-safe wake telemetry for diagnostics.
text = WORKER.read_text()
text = replace_once(
    text,
    '    last_media_source_id: str = ""\n    last_media_content_id_present: bool = False\n',
    '    last_media_source_id: str = ""\n    last_media_content_id_present: bool = False\n    last_uid_resolve_timeout_seconds: float = 0.0\n    last_uid_resolve_resend_interval_seconds: float = 0.0\n    last_uid_resolve_send_rounds: int = 0\n    last_uid_resolve_datagrams_sent: int = 0\n    last_uid_resolve_elapsed_ms: float | None = None\n    last_uid_resolve_succeeded: bool = False\n',
    "worker state trace fields",
)
text = replace_once(
    text,
    '    async def _process_once(self, event: CloudEvent) -> bool:\n',
    '    def _apply_uid_resolve_trace(self, trace) -> None:\n        """Copy only secret-safe UID wake timing/cadence telemetry."""\n        if trace is None:\n            return\n        self.state.last_uid_resolve_timeout_seconds = float(\n            getattr(trace, "timeout_seconds", 0.0) or 0.0\n        )\n        self.state.last_uid_resolve_resend_interval_seconds = float(\n            getattr(trace, "resend_interval_seconds", 0.0) or 0.0\n        )\n        self.state.last_uid_resolve_send_rounds = int(\n            getattr(trace, "send_rounds", 0) or 0\n        )\n        self.state.last_uid_resolve_datagrams_sent = int(\n            getattr(trace, "datagrams_sent", 0) or 0\n        )\n        self.state.last_uid_resolve_elapsed_ms = getattr(trace, "elapsed_ms", None)\n        self.state.last_uid_resolve_succeeded = bool(\n            getattr(trace, "succeeded", False)\n        )\n\n    async def _process_once(self, event: CloudEvent) -> bool:\n',
    "worker trace helper",
)
text = replace_once(
    text,
    '        self.state.last_file_size = 0\n        self.state.last_ready_event_fired = False\n',
    '        self.state.last_file_size = 0\n        self.state.last_ready_event_fired = False\n        self.state.last_uid_resolve_timeout_seconds = 0.0\n        self.state.last_uid_resolve_resend_interval_seconds = 0.0\n        self.state.last_uid_resolve_send_rounds = 0\n        self.state.last_uid_resolve_datagrams_sent = 0\n        self.state.last_uid_resolve_elapsed_ms = None\n        self.state.last_uid_resolve_succeeded = False\n',
    "worker trace reset",
)
text = replace_once(
    text,
    '        except CameraStageError as err:\n            apply_stream_probe_trace(\n',
    '        except CameraStageError as err:\n            self._apply_uid_resolve_trace(getattr(err, "uid_resolve_trace", None))\n            apply_stream_probe_trace(\n',
    "worker error trace",
)
text = replace_once(
    text,
    '        trace = result.stream_trace\n        apply_stream_probe_trace(self._entry.entry_id, trace)\n',
    '        self._apply_uid_resolve_trace(getattr(result, "uid_resolve_trace", None))\n        trace = result.stream_trace\n        apply_stream_probe_trace(self._entry.entry_id, trace)\n',
    "worker success trace",
)
WORKER.write_text(text)


# diagnostics.py: expose only non-sensitive wake telemetry and advance milestone.
text = DIAGNOSTICS.read_text()
text = replace_once(
    text,
    '            "last_media_content_id_present": bool(worker and worker.state.last_media_content_id_present),\n            "raw_path_exposed": False,\n',
    '            "last_media_content_id_present": bool(worker and worker.state.last_media_content_id_present),\n            "uid_resolve": {\n                "timeout_seconds": (\n                    worker.state.last_uid_resolve_timeout_seconds if worker else 0.0\n                ),\n                "resend_interval_seconds": (\n                    worker.state.last_uid_resolve_resend_interval_seconds if worker else 0.0\n                ),\n                "send_rounds": (\n                    worker.state.last_uid_resolve_send_rounds if worker else 0\n                ),\n                "datagrams_sent": (\n                    worker.state.last_uid_resolve_datagrams_sent if worker else 0\n                ),\n                "elapsed_ms": (\n                    worker.state.last_uid_resolve_elapsed_ms if worker else None\n                ),\n                "succeeded": bool(\n                    worker and worker.state.last_uid_resolve_succeeded\n                ),\n                "network_identifiers_exposed": False,\n            },\n            "raw_path_exposed": False,\n',
    "diagnostics uid trace",
)
text = replace_once(
    text,
    '        "milestone": "3B.14-post-auth-fresh-heartbeat-tid",\n',
    '        "milestone": "3B.15-uid-wake-reliability",\n',
    "diagnostics milestone",
)
DIAGNOSTICS.write_text(text)


# manifest version.
text = MANIFEST.read_text()
text = replace_once(
    text,
    '  "version": "0.1.2-beta.40"\n',
    '  "version": "0.1.2-beta.41"\n',
    "manifest version",
)
MANIFEST.write_text(text)


# Keep beta40 regression semantic instead of pinning the current release forever.
text = BETA40_TEST.read_text()
text = replace_once(
    text,
    '        for token in (\n            "p2p_heartbeat_fresh_tid_activated_after_login",\n            "p2p_heartbeat_pre_auth_reused_tid_count",\n            "3B.14-post-auth-fresh-heartbeat-tid",\n        ):\n            self.assertIn(token, diagnostics)\n        self.assertIn(\'"version": "0.1.2-beta.40"\', MANIFEST.read_text())\n',
    '        for token in (\n            "p2p_heartbeat_fresh_tid_activated_after_login",\n            "p2p_heartbeat_pre_auth_reused_tid_count",\n        ):\n            self.assertIn(token, diagnostics)\n',
    "beta40 future-compatible test",
)
BETA40_TEST.write_text(text)


BETA41_TEST.write_text('''"""Regression guards for beta.41 UID/LAN wake reliability."""\n\nfrom pathlib import Path\nimport unittest\n\nROOT = Path(__file__).parents[1]\nTRANSPORT = ROOT / "custom_components" / "reolink_battery" / "transport.py"\nBASE = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe.py"\nBETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"\nWORKER = ROOT / "custom_components" / "reolink_battery" / "recording_worker.py"\nDIAGNOSTICS = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"\nMANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"\n\n\nclass Beta41UidWakeReliabilityTests(unittest.TestCase):\n    def test_uid_discovery_uses_neolink_compatible_window_and_cadence(self):\n        source = TRANSPORT.read_text()\n        self.assertIn("UID_RESOLVE_TIMEOUT_SECONDS = 15.0", source)\n        self.assertIn("UID_RESOLVE_RESEND_SECONDS = 0.5", source)\n        self.assertIn("timeout: float = UID_RESOLVE_TIMEOUT_SECONDS", source)\n        self.assertIn("next_send = now + UID_RESOLVE_RESEND_SECONDS", source)\n\n    def test_uid_discovery_trace_is_secret_safe_and_propagated(self):\n        transport = TRANSPORT.read_text()\n        base = BASE.read_text()\n        worker = WORKER.read_text()\n        diagnostics = DIAGNOSTICS.read_text()\n        for token in (\n            "class UidResolveTrace",\n            "send_rounds",\n            "datagrams_sent",\n            "elapsed_ms",\n            "succeeded",\n        ):\n            self.assertIn(token, transport)\n        self.assertIn("uid_resolve_trace = UidResolveTrace", base)\n        self.assertIn("resolve_uid_lan, uid, interface, resolve_timeout, uid_resolve_trace", base)\n        self.assertIn("last_uid_resolve_send_rounds", worker)\n        self.assertIn('"network_identifiers_exposed": False', diagnostics)\n\n    def test_beta40_download_transport_is_untouched(self):\n        source = BETA20.read_text()\n        self.assertIn("PERIODIC_RX_ACK_INTERVAL = 0.010", source)\n        self.assertIn("P2P_HEARTBEAT_INTERVAL = 1.0", source)\n        self.assertIn("self._p2p_heartbeat_fresh_tid_enabled = False", source)\n        self.assertIn("activate_fresh_heartbeat_tids_after_login", source)\n        self.assertIn("RECORDING_SETTLE_SECONDS = 60.0", WORKER.read_text())\n\n    def test_diagnostics_and_version(self):\n        diagnostics = DIAGNOSTICS.read_text()\n        self.assertIn('"milestone": "3B.15-uid-wake-reliability"', diagnostics)\n        self.assertIn('"uid_resolve": {', diagnostics)\n        self.assertIn('"version": "0.1.2-beta.41"', MANIFEST.read_text())\n\n\nif __name__ == "__main__":\n    unittest.main()\n''')

print("beta41 patch applied")
