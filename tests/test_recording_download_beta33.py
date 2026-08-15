"""Regression guards for beta.33 reliable UDP download delivery."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
BETA20 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta20.py"
BETA21 = ROOT / "custom_components" / "reolink_battery" / "recording_download_probe_beta21.py"
DIAG = ROOT / "custom_components" / "reolink_battery" / "diagnostics.py"
MANIFEST = ROOT / "custom_components" / "reolink_battery" / "manifest.json"


def function_slice(source: str, marker: str, next_marker: str) -> str:
    start = source.index(marker)
    end = source.index(next_marker, start)
    return source[start:end]


class Beta33ReliableUdpTests(unittest.TestCase):
    def test_beta21_download_uses_reliable_sender_for_both_commands(self):
        source = BETA21.read_text()
        body = function_slice(source, "    async def send_file_download_probe(", "\n\ndef _build_cmd13_wire")
        self.assertEqual(body.count("_send_reliable_download_packet("), 2)
        self.assertNotIn("send_without_wait(", body)
        self.assertIn("arm_cmd8_delivery_future()", body)
        self.assertIn("_apply_udp_reliability_trace(trace)", body)

    def test_retransmit_reuses_exact_udp_packet_and_sequence(self):
        source = BETA20.read_text()
        body = function_slice(source, "    async def _send_reliable_download_packet(", "\n    def _apply_udp_reliability_trace")
        self.assertIn("udp_header = await self._construct_udp_header(len(data))", body)
        self.assertIn("packet = udp_header + data", body)
        self.assertEqual(body.count("_construct_udp_header"), 1)
        self.assertGreaterEqual(body.count("transport.sendto(packet"), 2)
        self.assertIn("RELIABLE_UDP_MAX_RETRANSMITS = 2", source)

    def test_protocol_has_ack_and_gap_instrumentation(self):
        source = BETA20.read_text()
        for needle in (
            "def parse_udp_ack",
            "udp_seq_gap_events",
            "udp_missing_packet_count",
            "udp_out_of_order_packets",
            "udp_duplicate_packets",
            "udp_ack_with_gap_bitmap_count",
            "udp_seq_at_remote_disconnect",
        ):
            self.assertIn(needle, source)

    def test_diagnostics_and_version(self):
        diag = DIAG.read_text()
        for needle in (
            '"cmd13_udp_ack_received"',
            '"cmd8_udp_ack_received"',
            '"cmd8_udp_retransmit_count"',
            '"udp_seq_gap_events"',
            '"udp_missing_packet_count"',
            '"udp_ack_sent_count"',
        ):
            self.assertIn(needle, diag)
        self.assertIn("3B.7-reliable-udp-retransmit", diag)
        self.assertIn('"version": "0.1.2-beta.33"', MANIFEST.read_text())


if __name__ == "__main__":
    unittest.main()
