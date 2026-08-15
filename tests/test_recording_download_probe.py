"""Offline tests for Milestone 3B.2b cmd13 preparation helpers."""

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

    def test_prepare_response_extracts_handle_size_and_name(self):
        response = """<?xml version=\"1.0\"?><body><FileInfoList><FileInfo><handle>7</handle><fileName>x.mp4</fileName><fileSize>123456</fileSize></FileInfo></FileInfoList></body>"""
        present, handle, size, name = probe._parse_prepare_response(response)
        self.assertTrue(present)
        self.assertTrue(handle)
        self.assertEqual(size, 123456)
        self.assertTrue(name)

    def test_empty_prepare_response_is_safe(self):
        self.assertEqual(
            probe._parse_prepare_response(""),
            (False, False, None, False),
        )

    def test_zero_size_is_not_treated_as_authoritative(self):
        response = """<?xml version=\"1.0\"?><body><FileInfo><size>0</size></FileInfo></body>"""
        present, handle, size, name = probe._parse_prepare_response(response)
        self.assertTrue(present)
        self.assertFalse(handle)
        self.assertIsNone(size)
        self.assertFalse(name)


if __name__ == "__main__":
    unittest.main()
