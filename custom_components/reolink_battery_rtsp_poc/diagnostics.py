"""Secret-safe diagnostics for Reolink Battery RTSP PoC."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import ReolinkBatteryRtspPocConfigEntry, source_entry_for
from .live_stream_diagnostics import live_probe_state


def _hex_class(value: int | None) -> str | None:
    return f"0x{value:04x}" if isinstance(value, int) else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ReolinkBatteryRtspPocConfigEntry
) -> dict[str, Any]:
    """Return only non-secret PoC telemetry."""
    source = source_entry_for(hass, entry.runtime_data.source_entry_id)
    live = live_probe_state(entry.entry_id)
    return {
        "source_integration": {
            "configured": source is not None,
            "loaded": bool(source is not None and source.runtime_data is not None),
            "credentials_reused_from_source": True,
            "credentials_exposed": False,
        },
        "live_stream_probe": {
            "experimental": True,
            "manual_only": True,
            "background_streaming_enabled": False,
            "bounded_duration_seconds": 10.0,
            "attempted": live.attempted,
            "success": live.success,
            "failure_stage": live.failure_stage or None,
            "failure_type": live.failure_type or None,
            "response_code": live.response_code,
            "stream_kind": live.stream_kind,
            "start_request": {
                "header_channel_id": live.start_request_header_channel_id,
                "stream_type": live.start_request_stream_type,
                "msg_num": live.start_request_msg_num,
                "message_class": _hex_class(live.start_request_message_class),
                "body_length": live.start_request_body_length,
                "payload_offset": live.start_request_payload_offset,
                "preview_handle": live.start_request_preview_handle,
                "preview_stream_type": live.start_request_preview_stream_type,
            },
            "stop_request": {
                "header_channel_id": live.stop_request_header_channel_id,
                "stream_type": live.stop_request_stream_type,
                "msg_num": live.stop_request_msg_num,
                "message_class": _hex_class(live.stop_request_message_class),
                "body_length": live.stop_request_body_length,
                "payload_offset": live.stop_request_payload_offset,
                "preview_handle": live.stop_request_preview_handle,
            },
            "start_attempted": live.start_attempted,
            "start_response_code": live.start_response_code,
            "start_accepted": live.start_accepted,
            "first_cmd3_delay_ms": live.first_cmd3_delay_ms,
            "cmd3_frames": live.cmd3_frames,
            "body_frames": live.body_frames,
            "total_body_bytes": live.total_body_bytes,
            "bcmedia_observed": live.bcmedia_observed,
            "bcmedia_info_frames": live.bcmedia_info_frames,
            "video_frames": live.video_frames,
            "iframe_frames": live.iframe_frames,
            "pframe_frames": live.pframe_frames,
            "h264_frames": live.h264_frames,
            "h265_frames": live.h265_frames,
            "unknown_body_frames": live.unknown_body_frames,
            "stop_attempted": live.stop_attempted,
            "stop_response_code": live.stop_response_code,
            "stop_accepted": live.stop_accepted,
            "heartbeat_count": live.heartbeat_count,
            "connection_lost_exception_present": live.connection_lost_exception_present,
            "elapsed_seconds": live.elapsed_seconds,
            "termination_reason": live.termination_reason or None,
            "uid_resolve": {
                "timeout_seconds": live.uid_resolve_timeout_seconds,
                "resend_interval_seconds": live.uid_resolve_resend_interval_seconds,
                "send_rounds": live.uid_resolve_send_rounds,
                "datagrams_sent": live.uid_resolve_datagrams_sent,
                "elapsed_ms": live.uid_resolve_elapsed_ms,
                "succeeded": live.uid_resolve_succeeded,
                "network_identifiers_exposed": False,
            },
            "raw_values_exposed": False,
        },
    }
