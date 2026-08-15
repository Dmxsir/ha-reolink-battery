"""Secret-safe diagnostics for Reolink Battery."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import ReolinkBatteryConfigEntry
from .const import CONF_AUTH_PATH, CONF_MODEL, CONF_UID
from .recording_download_probe import ROUTING_LAYOUT, download_prepare_state
from .recording_download_beta22 import stream_probe_state
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
    stream = stream_probe_state(entry.entry_id)
    worker = entry.runtime_data.recording_worker
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
            "sensor_state_change_count": bridge.sensor_state_change_count if bridge else 0,
            "matched_notification_update_count": (
                bridge.matched_notification_update_count if bridge else 0
            ),
            "last_sensor_state_change_time": (
                bridge.last_sensor_state_change_time.isoformat()
                if bridge and bridge.last_sensor_state_change_time
                else None
            ),
            "last_notification_post_time_ms": (
                bridge.last_notification_post_time_ms if bridge else None
            ),
            "last_post_time_changed": (
                bridge.last_post_time_changed if bridge else None
            ),
            "last_event_fingerprint": (
                bridge.last_event_fingerprint if bridge else ""
            ),
            "last_processing_lag_seconds": (
                bridge.last_processing_lag_seconds if bridge else None
            ),
            "telemetry_restored_from_pending": bool(
                bridge and bridge.telemetry_restored_from_pending
            ),
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
            "file_info": {
                "open_attempted": prepare.file_info_open_attempted,
                "open_succeeded": prepare.file_info_open_succeeded,
                "open_failure_type": prepare.file_info_open_failure_type or None,
                "open_response_code": prepare.file_info_open_response_code,
                "handle_present": prepare.file_info_handle_present,
                "get_attempted": prepare.file_info_get_attempted,
                "get_page_index": prepare.file_info_get_page_index,
                "get_pages_succeeded": prepare.file_info_get_pages_succeeded,
                "get_failure_type": prepare.file_info_get_failure_type or None,
                "get_response_code": prepare.file_info_get_response_code,
                "last_page_file_count": prepare.file_info_last_page_file_count,
                "finished_flag": prepare.file_info_finished_flag,
                "close_attempted": prepare.file_info_close_attempted,
                "close_succeeded": prepare.file_info_close_succeeded,
                "close_failure_type": prepare.file_info_close_failure_type or None,
                "close_response_code": prepare.file_info_close_response_code,
            },
            "identity": {
                "id_present": prepare.identity_id_present,
                "file_name_present": prepare.identity_file_name_present,
                "name_present": prepare.identity_name_present,
                "id_length": prepare.identity_id_length,
                "file_name_length": prepare.identity_file_name_length,
                "name_length": prepare.identity_name_length,
                "id_equals_file_name": prepare.identity_id_equals_file_name,
                "id_equals_name": prepare.identity_id_equals_name,
                "file_name_equals_name": prepare.identity_file_name_equals_name,
                "id_looks_like_path": prepare.identity_id_looks_like_path,
                "file_name_looks_like_path": prepare.identity_file_name_looks_like_path,
                "xml_channel_id_present": prepare.identity_xml_channel_id_present,
                "xml_channel_id_value": prepare.identity_xml_channel_id_value,
                "stream_type_present": prepare.identity_stream_type_present,
                "stream_type_value": prepare.identity_stream_type_value,
                "file_type_present": prepare.identity_file_type_present,
                "file_type_value": prepare.identity_file_type_value,
                "record_type_present": prepare.identity_record_type_present,
                "record_type_value": prepare.identity_record_type_value,
                "used_exact_id": prepare.identity_used_exact_id,
                "used_exact_file_name": prepare.identity_used_exact_file_name,
                "used_exact_name": prepare.identity_used_exact_name,
                "raw_values_exposed": False,
            },
            "routing_layout": ROUTING_LAYOUT,
            "request_header_channel_id": prepare.request_header_channel_id,
            "request_stream_type": prepare.request_stream_type,
            "request_msg_num": prepare.request_msg_num,
            "request_message_class": _hex_class(prepare.request_message_class),
            "request_body_length": prepare.request_body_length,
            "request_payload_offset": prepare.request_payload_offset,
            "response_message_class": _hex_class(prepare.response_message_class),
            "response_header_channel_id": prepare.response_header_channel_id,
            "response_stream_type": prepare.response_stream_type,
            "response_msg_num": prepare.response_msg_num,
            "response_body_length": prepare.response_body_length,
            "response_payload_offset": prepare.response_payload_offset,
            "routing_echo_match": (
                prepare.response_present
                and prepare.request_header_channel_id is not None
                and prepare.request_stream_type is not None
                and prepare.request_msg_num is not None
                and prepare.request_header_channel_id
                == prepare.response_header_channel_id
                and prepare.request_stream_type == prepare.response_stream_type
                and prepare.request_msg_num == prepare.response_msg_num
            ),
            "first_payload_length": prepare.first_payload_length,
            "first_body_present": prepare.first_payload_length > 0,
            "stream_probe": {
                "attempted": stream.attempted,
                "frame_count": stream.frame_count,
                "continuation_observed": stream.frame_count > 1,
                "total_body_bytes": stream.total_body_bytes,
                "cmd13_frames": stream.cmd13_frames,
                "cmd8_frames": stream.cmd8_frames,
                "cmd8_attempted": stream.cmd8_attempted,
                "cmd8_request_msg_num": stream.cmd8_request_msg_num,
                "cmd8_request_body_length": stream.cmd8_request_body_length,
                "cmd8_request_payload_offset": stream.cmd8_request_payload_offset,
                "cmd8_first_response_code": stream.cmd8_first_response_code,
                "cmd8_first_body_length": stream.cmd8_first_body_length,
                "cmd8_first_kind": stream.cmd8_first_kind or None,
                "cmd8_xml_frames": stream.cmd8_xml_frames,
                "cmd8_media_frames": stream.cmd8_media_frames,
                "cmd8_unknown_frames": stream.cmd8_unknown_frames,
                "cmd8_media_observed": stream.cmd8_media_observed,
                "keepalive_attempted": stream.keepalive_attempted,
                "keepalive_count": stream.keepalive_count,
                "keepalive_interval_seconds": stream.keepalive_interval_seconds,
                "p2p_heartbeat_attempted": stream.p2p_heartbeat_attempted,
                "p2p_heartbeat_count": stream.p2p_heartbeat_count,
                "p2p_heartbeat_interval_seconds": stream.p2p_heartbeat_interval_seconds,
                "p2p_heartbeat_started_after_handoff": (
                    stream.p2p_heartbeat_started_after_handoff
                ),
                "p2p_heartbeat_first_delay_seconds": (
                    stream.p2p_heartbeat_first_delay_seconds
                ),
                "p2p_heartbeat_pre_cmd13_count": (
                    stream.p2p_heartbeat_pre_cmd13_count
                ),
                "p2p_heartbeat_background_task_active": (
                    stream.p2p_heartbeat_background_task_active
                ),
                "proactive_cmd234_count": stream.proactive_cmd234_count,
                "cmd8_full_high": stream.cmd8_full_high,
                "cmd8_handle_used": stream.cmd8_handle_used,
                "cmd8_forced_main_stream": stream.cmd8_forced_main_stream,
                "cmd8_full_high_body_length": stream.cmd8_full_high_body_length,
                "cmd8_layout": stream.cmd8_layout or None,
                "file_write_attempted": stream.file_write_attempted,
                "part_created": stream.part_created,
                "file_bytes_written": stream.file_bytes_written,
                "fsync_completed": stream.fsync_completed,
                "mp4_ftyp_valid": stream.mp4_ftyp_valid,
                "final_size": stream.final_size,
                "final_size_match": stream.final_size_match,
                "atomic_rename_completed": stream.atomic_rename_completed,
                "file_saved": stream.file_saved,
                "part_removed_on_failure": stream.part_removed_on_failure,
                "sha256_present": stream.sha256_present,
                "output_private_path": stream.output_private_path,
                "telemetry_owner": stream.telemetry_owner,
                "telemetry_event_time": (
                    stream.telemetry_event_time.isoformat()
                    if stream.telemetry_event_time
                    else None
                ),
                "single_lease_handoff": stream.single_lease_handoff,
                "single_lease_socket_reused": stream.single_lease_socket_reused,
                "single_lease_ids_reused": stream.single_lease_ids_reused,
                "single_lease_transaction_id_reused": (
                    stream.single_lease_transaction_id_reused
                ),
                "secondary_connect_sent": stream.secondary_connect_sent,
                "remote_disconnect_observed": stream.remote_disconnect_observed,
                "connection_lost_exception_present": stream.connection_lost_exception_present,
                "decoded_cmd8_chunks": stream.decoded_cmd8_chunks,
                "decoded_cmd8_bytes": stream.decoded_cmd8_bytes,
                "raw_cmd8_chunks": stream.raw_cmd8_chunks,
                "nonzero_payload_offset_frames": stream.nonzero_payload_offset_frames,
                "aggregate_bytes": stream.aggregate_bytes,
                "aggregate_limit_bytes": stream.aggregate_limit_bytes,
                "mp4_offset_found": stream.mp4_offset_found,
                "mp4_offset": stream.mp4_offset,
                "mp4_bytes_collected": stream.mp4_bytes_collected,
                "expected_size_reached": stream.expected_size_reached,
                "expected_size_match": stream.expected_size_match,
                "bcmedia_prefix_bytes": stream.bcmedia_prefix_bytes,
                "distinct_msg_num_count": stream.distinct_msg_num_count,
                "max_body_length": stream.max_body_length,
                "response_codes": list(stream.response_codes),
                "completion_code": stream.completion_code,
                "termination_reason": stream.termination_reason or None,
                "elapsed_seconds": stream.elapsed_seconds,
                "first_frame_kind": stream.first_frame_kind or None,
                "xml_frames": stream.xml_frames,
                "media_frames": stream.media_frames,
                "mp4_frames": stream.mp4_frames,
                "bcmedia_frames": stream.bcmedia_frames,
                "unknown_frames": stream.unknown_frames,
                "aes_decrypted_frames": stream.aes_decrypted_frames,
                "media_bytes_observed": stream.media_bytes_observed,
                "xml_reported_size": stream.xml_reported_size,
                "xml_file_info_present": stream.xml_file_info_present,
                "xml_handle_present": stream.xml_handle_present,
                "xml_binary_data_present": stream.xml_binary_data_present,
                "sample_limit_bytes": stream.sample_limit_bytes,
                "sample_limit_frames": stream.sample_limit_frames,
                "sample_limit_reached": stream.termination_reason
                in {"byte_limit", "frame_limit"},
                "raw_values_exposed": False,
            },
            "media_payload_observed": stream.media_frames > 0,
            "full_media_download_attempted": stream.file_write_attempted,
            "cmd8_attempted": stream.cmd8_attempted,
        },
        "recording_worker": {
            "configured": worker is not None,
            "enabled": bool(worker and worker.state.enabled),
            "running": bool(worker and worker.state.running),
            "pending_trigger": bool(worker and worker.state.pending_trigger),
            "waiting_camera_closed": bool(worker and worker.state.waiting_camera_closed),
            "settle_seconds": worker.state.settle_seconds if worker else None,
            "attempts": worker.state.attempts if worker else 0,
            "retries": worker.state.retries if worker else 0,
            "completed": worker.state.completed if worker else 0,
            "last_event_time": (
                worker.state.last_event_time.isoformat()
                if worker and worker.state.last_event_time else None
            ),
            "last_attempt_time": (
                worker.state.last_attempt_time.isoformat()
                if worker and worker.state.last_attempt_time else None
            ),
            "last_completed_time": (
                worker.state.last_completed_time.isoformat()
                if worker and worker.state.last_completed_time else None
            ),
            "last_failure_stage": (worker.state.last_failure_stage or None) if worker else None,
            "last_failure_type": (worker.state.last_failure_type or None) if worker else None,
            "last_file_saved": bool(worker and worker.state.last_file_saved),
            "last_file_size": worker.state.last_file_size if worker else 0,
            "last_ready_event_fired": bool(worker and worker.state.last_ready_event_fired),
            "last_media_source_id": (worker.state.last_media_source_id or None) if worker else None,
            "last_media_content_id_present": bool(worker and worker.state.last_media_content_id_present),
            "raw_path_exposed": False,
        },
        "milestone": "3B.6-continuous-p2p-heartbeat",
        "camera_worker_enabled": worker is not None,
        "automatic_recording_processing_enabled": worker is not None,
    }