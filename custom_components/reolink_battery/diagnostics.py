"""Secret-safe diagnostics for Reolink Battery."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import ReolinkBatteryConfigEntry
from .const import CONF_AUTH_PATH, CONF_MODEL, CONF_UID
from .recording_download_probe import download_prepare_state
from .recording_probe import probe_state


def _redacted_uid(uid: str) -> str:
    if len(uid) <= 6:
        return "***"
    return f"{uid[:3]}…{uid[-3:]}"


def _hex_class(value: int | None) -> str | None:
    return f"0x{value:04x}" if isinstance(value, int) else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> dict[str, Any]:
    """Return no credentials, tokens, network addresses, filenames, or session keys."""
    coordinator = entry.runtime_data.coordinator
    bridge = entry.runtime_data.notification_bridge
    recording = probe_state(entry.entry_id)
    prepare = download_prepare_state(entry.entry_id)
    return {
        "device": {
            "model": entry.data.get(CONF_MODEL, ""),
            "auth_path": entry.data.get(CONF_AUTH_PATH, ""),
            "uid": _redacted_uid(entry.data.get(CONF_UID, "")),
        },
        "events": {
            "last_successful_event_time": (
                coordinator.last_successful_event_time.isoformat()
                if coordinator.last_successful_event_time
                else None
            ),
            "last_poll_time": (
                coordinator.last_poll_time.isoformat()
                if coordinator.last_poll_time
                else None
            ),
            "pending_count": len(coordinator.pending_events),
            "processed_count": coordinator.processed_event_count,
            "last_failure_stage": coordinator.last_failure_stage or None,
            "last_failure_type": coordinator.last_failure_type or None,
            "last_failure_reason": coordinator.last_failure_reason or None,
            "cloud_user_id_present": coordinator.cloud_user_id_present,
            "message_center_http_status": coordinator.last_http_status,
            "message_center_wrapped": coordinator.last_response_wrapped,
            "message_center_items": coordinator.last_item_count,
            "message_center_next_token_present": (
                coordinator.last_next_token_present
            ),
            "last_cloud_event_type": coordinator.last_event_type or None,
            "last_cloud_event_ai_types": list(coordinator.last_event_ai_types),
            "last_event_id_present": coordinator.last_event_id_present,
            "last_event_uid_match": coordinator.last_event_uid_match,
            "last_event_queued": coordinator.last_event_queued,
        },
        "notification_bridge": {
            "configured": bridge is not None,
            "listener_active": bool(bridge and bridge.active),
            "last_reolink_notification_time": (
                bridge.last_reolink_notification_time.isoformat()
                if bridge and bridge.last_reolink_notification_time
                else None
            ),
            "last_reolink_notification_camera": (
                bridge.last_reolink_notification_camera if bridge else ""
            ),
            "last_event_matched": bool(bridge and bridge.last_event_matched),
            "last_camera_mapped": bool(bridge and bridge.last_camera_mapped),
            "last_event_queued": bool(bridge and bridge.last_event_queued),
            "duplicate_rejected": bool(bridge and bridge.last_duplicate_rejected),
        },
        "recording_probe": {
            "manual_only": True,
            "attempted": recording.attempted,
            "success": recording.success,
            "event_time": (
                recording.event_time.isoformat() if recording.event_time else None
            ),
            "candidate_start": (
                recording.candidate_start.isoformat()
                if recording.candidate_start
                else None
            ),
            "candidate_end": (
                recording.candidate_end.isoformat()
                if recording.candidate_end
                else None
            ),
            "candidate_size": recording.candidate_size,
            "candidate_distance_seconds": recording.candidate_distance_seconds,
            "candidate_name_present": recording.candidate_name_present,
            "failure_stage": recording.failure_stage or None,
            "download_attempted": False,
        },
        "download_prepare": {
            "manual_only": True,
            "attempted": prepare.attempted,
            "success": prepare.success,
            "event_time": (
                prepare.event_time.isoformat() if prepare.event_time else None
            ),
            "candidate_start": (
                prepare.candidate_start.isoformat()
                if prepare.candidate_start
                else None
            ),
            "candidate_end": (
                prepare.candidate_end.isoformat()
                if prepare.candidate_end
                else None
            ),
            "candidate_distance_seconds": prepare.candidate_distance_seconds,
            "response_present": prepare.response_present,
            "response_accepted": prepare.response_accepted,
            "failure_stage": prepare.failure_stage or None,
            "failure_type": prepare.failure_type or None,
            "response_code": prepare.response_code,
            "request_channel_id": prepare.request_channel_id,
            "request_stream_type": prepare.request_stream_type,
            "request_msg_num": prepare.request_msg_num,
            "request_message_class": _hex_class(prepare.request_message_class),
            "request_body_length": prepare.request_body_length,
            "request_payload_offset": prepare.request_payload_offset,
            "response_message_class": _hex_class(prepare.response_message_class),
            "response_channel_id": prepare.response_channel_id,
            "response_stream_type": prepare.response_stream_type,
            "response_msg_num": prepare.response_msg_num,
            "response_body_length": prepare.response_body_length,
            "response_payload_offset": prepare.response_payload_offset,
            "first_payload_length": prepare.first_payload_length,
            "media_payload_observed": prepare.first_payload_length > 0,
            "full_media_download_attempted": False,
            "cmd8_attempted": False,
        },
        "milestone": "3B.2b-frame-probe",
        "camera_worker_enabled": False,
        "automatic_recording_processing_enabled": False,
    }
