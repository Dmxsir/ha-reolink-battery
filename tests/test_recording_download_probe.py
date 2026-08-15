"""Offline tests for Milestone 3B.2b native cmd13 frame probe."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_download_probe_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
probe = importlib.import_module(f"{PACKAGE}.recording_download_probe")


class _FakeBaichuan:
    def __init__(self, mess_id: int = 10) -> None:
        self._mess_id = mess_id

    @staticmethod
    def _aes_encrypt(data: bytes) -> bytes:
        # Identity transform lets the unit test inspect framing lengths only.
        # Real reolink-aio AES-CFB preserves the same byte length.
        return data


class DownloadPrepareHelperTests(unittest.TestCase):
    def test_download_xml_contains_required_identity(self):
        xml = probe._download_xml("ABC123", "/mnt/sda/a&b/test<1>.mp4")
        self.assertIn("<channelId>0</channelId>", xml)
        self.assertIn("<uid>ABC123</uid>", xml)
        self.assertIn("/mnt/sda/a&amp;b/test&lt;1&gt;.mp4", xml)
        self.assertIn("<name>test&lt;1&gt;.mp4</name>", xml)
        self.assertIn("<Id>/mnt/sda/a&amp;b/test&lt;1&gt;.mp4</Id>", xml)

    def test_binary_extension_marks_binary_data(self):
        xml = probe._binary_extension_xml()
        self.assertIn("<binaryData>1</binaryData>", xml)
        self.assertIn("<channelId>0</channelId>", xml)

    def test_cmd13_wire_uses_file_download_header(self):
        baichuan = _FakeBaichuan(10)
        wire, meta = probe._build_cmd13_wire(
            baichuan, "ABC123", "/mnt/sda/recording.mp4"
        )

        self.assertEqual(wire[0:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 13)
        self.assertEqual(int.from_bytes(wire[8:12], "little"), meta.body_length)
        self.assertEqual(wire[12], 1)
        self.assertEqual(wire[13], 0)
        self.assertEqual(int.from_bytes(wire[14:16], "little"), 11)
        self.assertEqual(int.from_bytes(wire[16:18], "little"), 0)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6482)
        self.assertEqual(int.from_bytes(wire[20:24], "little"), meta.payload_offset)
        self.assertEqual(len(wire), 24 + meta.body_length)
        self.assertEqual(meta.channel_id, 1)
        self.assertEqual(meta.stream_type, 0)
        self.assertEqual(meta.msg_num, 11)
        self.assertEqual(meta.message_class, 0x6482)
        self.assertEqual(baichuan._mess_id, 11)

    def test_payload_offset_is_binary_extension_length(self):
        baichuan = _FakeBaichuan()
        _, meta = probe._build_cmd13_wire(
            baichuan, "ABC123", "/mnt/sda/recording.mp4"
        )
        self.assertEqual(
            meta.payload_offset,
            len(probe._binary_extension_xml().encode("utf-8")),
        )

    def test_message_number_wraps_without_zero(self):
        baichuan = _FakeBaichuan(0xFFFF)
        _, meta = probe._build_cmd13_wire(
            baichuan, "ABC123", "/mnt/sda/recording.mp4"
        )
        self.assertEqual(meta.msg_num, 1)
        self.assertEqual(baichuan._mess_id, 1)

    def test_known_prepare_response_codes_are_explicit(self):
        self.assertEqual(
            probe.ACCEPTED_PREPARE_RESPONSE_CODES,
            frozenset({0, 200, 201, 300}),
        )

    def test_prepare_error_keeps_only_safe_protocol_metadata(self):
        err = probe.DownloadPrepareError(
            "DOWNLOAD_PREPARE_ERROR",
            failure_type="ApiError",
            response_code=400,
        )
        self.assertEqual(err.stage, "DOWNLOAD_PREPARE_ERROR")
        self.assertEqual(err.failure_type, "ApiError")
        self.assertEqual(err.response_code, 400)


if __name__ == "__main__":
    unittest.main()
