"""Offline tests for Milestone 3B.2b cmd13 identity probe."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_probe_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe")
recording_probe = importlib.import_module(f"{PACKAGE}.recording_probe")


class _FakeBaichuan:
    def __init__(self, mess_id: int = 10) -> None:
        self._mess_id = mess_id

    @staticmethod
    def _aes_encrypt(data: bytes) -> bytes:
        # Identity transform lets the unit test inspect framing lengths only.
        # Real reolink-aio AES-CFB preserves the same byte length.
        return data


def _candidate(**overrides):
    values = {
        "file_name": "/mnt/sda/a&b/test<1>.mp4",
        "start_time": datetime(2026, 8, 15, 10, 22, 54),
        "end_time": datetime(2026, 8, 15, 10, 23, 28),
        "size": 0,
        "distance_seconds": 4.792,
        # Exact beta.9 Argus shape: path-like Id, no fileName, short name.
        "record_id": "/mnt/sda/a&b/test<1>.mp4",
        "xml_file_name": "",
        "display_name": "short-record-id",
        "channel_id": 0,
        "stream_type": "mainStream",
        "file_type": "mp4",
        "record_type": "io, md",
    }
    values.update(overrides)
    return recording_probe.RecordingCandidate(**values)


class DownloadPrepareHelperTests(unittest.TestCase):
    def test_download_xml_keeps_distinct_fields(self):
        xml = probe._download_xml(
            "ABC123",
            channel_id=0,
            record_id="short-record-id",
            file_name="/mnt/sda/a&b/test<1>.mp4",
            display_name="short-record-id",
        )
        self.assertIn("<channelId>0</channelId>", xml)
        self.assertIn("<uid>ABC123</uid>", xml)
        self.assertIn("<fileName>/mnt/sda/a&amp;b/test&lt;1&gt;.mp4</fileName>", xml)
        self.assertIn("<name>short-record-id</name>", xml)
        self.assertIn("<Id>short-record-id</Id>", xml)

    def test_argus_shape_remaps_name_to_id_and_path_id_to_filename(self):
        _, record_id, file_name, display_name, trace = probe._resolve_download_identity(
            _candidate()
        )
        self.assertEqual(record_id, "short-record-id")
        self.assertEqual(file_name, "/mnt/sda/a&b/test<1>.mp4")
        self.assertEqual(display_name, "short-record-id")
        self.assertTrue(trace.id_present)
        self.assertFalse(trace.file_name_present)
        self.assertTrue(trace.name_present)
        self.assertTrue(trace.id_looks_like_path)
        self.assertFalse(trace.file_name_looks_like_path)
        self.assertEqual(trace.xml_channel_id_value, 0)
        self.assertEqual(trace.stream_type_value, "mainStream")
        self.assertEqual(trace.file_type_value, "mp4")
        self.assertEqual(trace.record_type_value, "io, md")
        self.assertTrue(trace.used_exact_id)
        self.assertFalse(trace.used_exact_file_name)
        self.assertTrue(trace.used_exact_name)

    def test_normal_shape_preserves_beta9_mapping(self):
        candidate = _candidate(
            record_id="record-identity",
            xml_file_name="/mnt/sda/normal.mp4",
            display_name="normal.mp4",
            file_name="record-identity",
        )
        _, record_id, file_name, display_name, _ = probe._resolve_download_identity(
            candidate
        )
        self.assertEqual(record_id, "record-identity")
        self.assertEqual(file_name, "/mnt/sda/normal.mp4")
        self.assertEqual(display_name, "normal.mp4")

    def test_identity_fallbacks_do_not_claim_exact_fields(self):
        candidate = _candidate(
            file_name="fallback.mp4",
            record_id="",
            xml_file_name="",
            display_name="",
            channel_id=None,
            stream_type="",
            file_type="",
            record_type="",
        )
        channel, record_id, file_name, display_name, trace = (
            probe._resolve_download_identity(candidate)
        )
        self.assertEqual(channel, 0)
        self.assertEqual(record_id, "fallback.mp4")
        self.assertEqual(file_name, "fallback.mp4")
        self.assertEqual(display_name, "fallback.mp4")
        self.assertFalse(trace.id_present)
        self.assertFalse(trace.file_name_present)
        self.assertFalse(trace.name_present)
        self.assertFalse(trace.used_exact_id)
        self.assertFalse(trace.used_exact_file_name)
        self.assertFalse(trace.used_exact_name)

    def test_binary_extension_is_unchanged(self):
        xml = probe._binary_extension_xml()
        self.assertIn("<binaryData>1</binaryData>", xml)
        self.assertIn("<channelId>0</channelId>", xml)

    def test_cmd13_wire_uses_channel_stream_msgnum16_layout(self):
        baichuan = _FakeBaichuan(10)
        wire, meta, identity = probe._build_cmd13_wire(
            baichuan, "ABC123", _candidate()
        )

        self.assertEqual(wire[0:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 13)
        self.assertEqual(int.from_bytes(wire[8:12], "little"), meta.body_length)
        self.assertEqual(wire[12], 7)
        self.assertEqual(wire[13], 0)
        self.assertEqual(int.from_bytes(wire[14:16], "little"), 11)
        self.assertEqual(int.from_bytes(wire[16:18], "little"), 0)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6482)
        self.assertEqual(int.from_bytes(wire[20:24], "little"), meta.payload_offset)
        self.assertEqual(len(wire), 24 + meta.body_length)
        self.assertEqual(meta.header_channel_id, 7)
        self.assertEqual(meta.stream_type, 0)
        self.assertEqual(meta.msg_num, 11)
        self.assertEqual(meta.message_class, 0x6482)
        self.assertEqual(baichuan._mess_id, 11)
        self.assertTrue(identity.used_exact_id)
        self.assertTrue(identity.used_exact_name)

    def test_cmd13_payload_contains_remapped_argus_values(self):
        baichuan = _FakeBaichuan(2)
        wire, meta, _ = probe._build_cmd13_wire(
            baichuan, "ABC123", _candidate()
        )
        payload = wire[24 + meta.payload_offset :].decode("utf-8")
        self.assertIn("<Id>short-record-id</Id>", payload)
        self.assertIn("<fileName>/mnt/sda/a&amp;b/test&lt;1&gt;.mp4</fileName>", payload)
        self.assertIn("<name>short-record-id</name>", payload)
        self.assertNotIn("<Id>/mnt/sda/", payload)

    def test_msg_num_uses_full_16_bits(self):
        baichuan = _FakeBaichuan(0x1233)
        wire, meta, _ = probe._build_cmd13_wire(
            baichuan, "ABC123", _candidate()
        )
        self.assertEqual(meta.msg_num, 0x1234)
        self.assertEqual(wire[12:16], bytes([7, 0, 0x34, 0x12]))

    def test_payload_offset_is_binary_extension_length(self):
        baichuan = _FakeBaichuan()
        _, meta, _ = probe._build_cmd13_wire(
            baichuan, "ABC123", _candidate()
        )
        self.assertEqual(
            meta.payload_offset,
            len(probe._binary_extension_xml().encode("utf-8")),
        )

    def test_msg_num_wraps_at_16_bits(self):
        baichuan = _FakeBaichuan((1 << 16) - 1)
        wire, meta, _ = probe._build_cmd13_wire(
            baichuan, "ABC123", _candidate()
        )
        self.assertEqual(meta.msg_num, 0)
        self.assertEqual(baichuan._mess_id, 0)
        self.assertEqual(wire[12:16], bytes([7, 0, 0, 0]))

    def test_routing_layout_is_explicit(self):
        self.assertEqual(
            probe.ROUTING_LAYOUT,
            "file_download_ch_stream_msgnum16",
        )

    def test_identity_mapping_is_explicit(self):
        self.assertEqual(
            probe.IDENTITY_MAPPING,
            "argus_name_to_id__id_to_file_name",
        )

    def test_known_prepare_response_codes_are_explicit(self):
        self.assertEqual(
            probe.ACCEPTED_PREPARE_RESPONSE_CODES,
            frozenset({0, 200, 201, 300}),
        )

    def test_prepare_error_keeps_only_safe_protocol_metadata(self):
        trace = recording_probe.FileInfoTrace(
            open_attempted=True,
            open_succeeded=True,
            handle_present=True,
            get_attempted=True,
            get_page_index=0,
            get_failure_type="ApiError",
            get_response_code=400,
            close_attempted=True,
            close_succeeded=True,
        )
        err = probe.DownloadPrepareError(
            "FILE_INFO_GET_ERROR",
            failure_type="ApiError",
            response_code=400,
            file_info_trace=trace,
        )
        self.assertEqual(err.stage, "FILE_INFO_GET_ERROR")
        self.assertEqual(err.failure_type, "ApiError")
        self.assertEqual(err.response_code, 400)
        self.assertIs(err.file_info_trace, trace)

    def test_file_info_trace_copies_to_secret_safe_state(self):
        trace = recording_probe.FileInfoTrace(
            open_attempted=True,
            open_succeeded=True,
            handle_present=True,
            get_attempted=True,
            get_page_index=0,
            get_pages_succeeded=0,
            get_failure_type="TimeoutError",
            get_response_code=None,
            close_attempted=True,
            close_succeeded=False,
            close_failure_type="ApiError",
            close_response_code=400,
        )
        state = probe.DownloadPrepareState()
        probe.apply_file_info_trace(state, trace)
        self.assertTrue(state.file_info_open_succeeded)
        self.assertTrue(state.file_info_handle_present)
        self.assertEqual(state.file_info_get_page_index, 0)
        self.assertEqual(state.file_info_get_failure_type, "TimeoutError")
        self.assertTrue(state.file_info_close_attempted)
        self.assertFalse(state.file_info_close_succeeded)
        self.assertEqual(state.file_info_close_response_code, 400)

    def test_identity_trace_copies_to_secret_safe_state(self):
        *_, trace = probe._resolve_download_identity(_candidate())
        state = probe.DownloadPrepareState()
        probe.apply_identity_trace(state, trace)
        self.assertTrue(state.identity_id_present)
        self.assertFalse(state.identity_file_name_present)
        self.assertTrue(state.identity_name_present)
        self.assertEqual(state.identity_stream_type_value, "mainStream")
        self.assertTrue(state.identity_used_exact_id)
        self.assertFalse(state.identity_used_exact_file_name)
        self.assertTrue(state.identity_used_exact_name)


if __name__ == "__main__":
    unittest.main()
