"""Offline tests for Milestone 3B.2b cmd13 routing probe."""

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
    def test_download_xml_is_unchanged_from_beta5(self):
        xml = probe._download_xml("ABC123", "/mnt/sda/a&b/test<1>.mp4")
        self.assertIn("<channelId>0</channelId>", xml)
        self.assertIn("<uid>ABC123</uid>", xml)
        self.assertIn("/mnt/sda/a&amp;b/test&lt;1&gt;.mp4", xml)
        self.assertIn("<name>test&lt;1&gt;.mp4</name>", xml)
        self.assertIn("<Id>/mnt/sda/a&amp;b/test&lt;1&gt;.mp4</Id>", xml)

    def test_binary_extension_is_unchanged_from_beta5(self):
        xml = probe._binary_extension_xml()
        self.assertIn("<binaryData>1</binaryData>", xml)
        self.assertIn("<channelId>0</channelId>", xml)

    def test_cmd13_wire_uses_reolink_aio_routing_layout(self):
        baichuan = _FakeBaichuan(10)
        wire, meta = probe._build_cmd13_wire(
            baichuan, "ABC123", "/mnt/sda/recording.mp4"
        )

        self.assertEqual(wire[0:4], bytes.fromhex("f0debc0a"))
        self.assertEqual(int.from_bytes(wire[4:8], "little"), 13)
        self.assertEqual(int.from_bytes(wire[8:12], "little"), meta.body_length)
        self.assertEqual(wire[12], 7)
        self.assertEqual(int.from_bytes(wire[13:16], "little"), 11)
        self.assertEqual(int.from_bytes(wire[12:16], "little"), meta.full_message_id)
        self.assertEqual(int.from_bytes(wire[16:18], "little"), 0)
        self.assertEqual(int.from_bytes(wire[18:20], "little"), 0x6482)
        self.assertEqual(int.from_bytes(wire[20:24], "little"), meta.payload_offset)
        self.assertEqual(len(wire), 24 + meta.body_length)
        self.assertEqual(meta.header_channel_id, 7)
        self.assertEqual(meta.message_id, 11)
        self.assertEqual(meta.message_class, 0x6482)
        self.assertEqual(baichuan._mess_id, 11)

    def test_full_message_id_matches_pinned_library_encoding(self):
        baichuan = _FakeBaichuan(0x123455)
        wire, meta = probe._build_cmd13_wire(
            baichuan, "ABC123", "/mnt/sda/recording.mp4"
        )
        self.assertEqual(meta.message_id, 0x123456)
        expected_routing = bytes([7]) + (0x123456).to_bytes(3, "little")
        self.assertEqual(wire[12:16], expected_routing)
        self.assertEqual(meta.full_message_id, int.from_bytes(expected_routing, "little"))

    def test_payload_offset_is_binary_extension_length(self):
        baichuan = _FakeBaichuan()
        _, meta = probe._build_cmd13_wire(
            baichuan, "ABC123", "/mnt/sda/recording.mp4"
        )
        self.assertEqual(
            meta.payload_offset,
            len(probe._binary_extension_xml().encode("utf-8")),
        )

    def test_message_id_wraps_exactly_like_pinned_reolink_aio(self):
        baichuan = _FakeBaichuan((1 << 24) - 1)
        wire, meta = probe._build_cmd13_wire(
            baichuan, "ABC123", "/mnt/sda/recording.mp4"
        )
        self.assertEqual(meta.message_id, 0)
        self.assertEqual(baichuan._mess_id, 0)
        self.assertEqual(wire[12:16], bytes([7, 0, 0, 0]))

    def test_routing_layout_is_explicit(self):
        self.assertEqual(
            probe.ROUTING_LAYOUT,
            "reolink_aio_ch_plus_message_id24",
        )

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
