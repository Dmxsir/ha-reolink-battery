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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> dict[str, Any]:
    """Return no credentials, tokens, network addresses, or session material."""
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
            "handle_present": prepare.handle_present,
            "expected_size": prepare.expected_size,
            "response_file_name_present": prepare.response_file_name_present,
            "failure_stage": prepare.failure_stage or None,
            "failure_type": prepare.failure_type or None,
            "response_code": prepare.response_code,
            "header_channel_id": 7,
            "message_id_forced": False,
            "media_transfer_attempted": False,
        },
        "milestone": "3B.2b-prep",
        "camera_worker_enabled": False,
        "automatic_recording_processing_enabled": False,
    }
