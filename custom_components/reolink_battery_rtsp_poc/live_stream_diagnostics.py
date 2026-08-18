"""Secret-safe telemetry storage for the isolated live-view PoC."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class LiveProbeState:
    attempted: bool = False
    success: bool = False
    failure_stage: str = ""
    failure_type: str = ""
    response_code: int | None = None
    stream_kind: str = "main"
    start_request_header_channel_id: int | None = None
    start_request_stream_type: int | None = None
    start_request_msg_num: int | None = None
    start_request_message_class: int | None = None
    start_request_body_length: int | None = None
    start_request_payload_offset: int | None = None
    start_request_preview_handle: int | None = None
    start_request_preview_stream_type: str | None = None
    stop_request_header_channel_id: int | None = None
    stop_request_stream_type: int | None = None
    stop_request_msg_num: int | None = None
    stop_request_message_class: int | None = None
    stop_request_body_length: int | None = None
    stop_request_payload_offset: int | None = None
    stop_request_preview_handle: int | None = None
    start_attempted: bool = False
    start_response_code: int | None = None
    start_accepted: bool = False
    first_cmd3_delay_ms: float | None = None
    cmd3_frames: int = 0
    body_frames: int = 0
    total_body_bytes: int = 0
    bcmedia_observed: bool = False
    bcmedia_info_frames: int = 0
    video_frames: int = 0
    iframe_frames: int = 0
    pframe_frames: int = 0
    h264_frames: int = 0
    h265_frames: int = 0
    unknown_body_frames: int = 0
    stop_attempted: bool = False
    stop_response_code: int | None = None
    stop_accepted: bool = False
    heartbeat_count: int = 0
    connection_lost_exception_present: bool = False
    elapsed_seconds: float | None = None
    termination_reason: str = ""
    uid_resolve_timeout_seconds: float = 0.0
    uid_resolve_resend_interval_seconds: float = 0.0
    uid_resolve_send_rounds: int = 0
    uid_resolve_datagrams_sent: int = 0
    uid_resolve_elapsed_ms: float | None = None
    uid_resolve_succeeded: bool = False


_STATES: dict[str, LiveProbeState] = {}


def live_probe_state(entry_id: str) -> LiveProbeState:
    return _STATES.setdefault(entry_id, LiveProbeState())


def reset_live_probe_state(entry_id: str, *, stream_kind: str = "main") -> None:
    _STATES[entry_id] = LiveProbeState(attempted=True, stream_kind=stream_kind)


def _apply_uid(state: LiveProbeState, uid_trace: Any | None) -> None:
    if uid_trace is None:
        return
    state.uid_resolve_timeout_seconds = float(getattr(uid_trace, "timeout_seconds", 0.0))
    state.uid_resolve_resend_interval_seconds = float(getattr(uid_trace, "resend_interval_seconds", 0.0))
    state.uid_resolve_send_rounds = int(getattr(uid_trace, "send_rounds", 0))
    state.uid_resolve_datagrams_sent = int(getattr(uid_trace, "datagrams_sent", 0))
    state.uid_resolve_elapsed_ms = getattr(uid_trace, "elapsed_ms", None)
    state.uid_resolve_succeeded = bool(getattr(uid_trace, "succeeded", False))


def _apply_trace(state: LiveProbeState, trace: Any | None) -> None:
    if trace is None:
        return
    for name in (
        "stream_kind", "start_attempted", "start_response_code", "start_accepted",
        "first_cmd3_delay_ms", "cmd3_frames", "body_frames", "total_body_bytes",
        "bcmedia_observed", "bcmedia_info_frames", "video_frames", "iframe_frames",
        "pframe_frames", "h264_frames", "h265_frames", "unknown_body_frames",
        "stop_attempted", "stop_response_code", "stop_accepted", "heartbeat_count",
        "connection_lost_exception_present", "elapsed_seconds", "termination_reason",
    ):
        setattr(state, name, copy.deepcopy(getattr(trace, name)))


def apply_live_probe_result(entry_id: str, result: Any) -> None:
    state = live_probe_state(entry_id)
    state.attempted = True
    state.failure_stage = ""
    state.failure_type = ""
    state.response_code = None
    _apply_trace(state, result.trace)
    _apply_uid(state, result.uid_resolve_trace)
    for prefix, request in (("start_request", result.start_request), ("stop_request", result.stop_request)):
        for field in (
            "header_channel_id", "stream_type", "msg_num", "message_class",
            "body_length", "payload_offset", "preview_handle",
        ):
            setattr(state, f"{prefix}_{field}", getattr(request, field))
    state.start_request_preview_stream_type = result.start_request.preview_stream_type
    state.success = bool(result.trace.start_accepted and result.trace.bcmedia_observed)
    if not state.success:
        state.failure_stage = "LIVE_MEDIA_NOT_OBSERVED" if result.trace.start_accepted else "LIVE_STREAM_REJECTED"


def apply_live_probe_error(entry_id: str, error: Any) -> None:
    state = live_probe_state(entry_id)
    state.attempted = True
    state.success = False
    state.failure_stage = getattr(error, "stage", "LIVE_STREAM_ERROR")
    state.failure_type = getattr(error, "failure_type", "")
    response = getattr(error, "response_code", None)
    state.response_code = response if isinstance(response, int) else None
    _apply_trace(state, getattr(error, "trace", None))
    _apply_uid(state, getattr(error, "uid_resolve_trace", None))
