"""Secret-safe runtime diagnostics state for the experimental live-view PoC."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class LiveProbeDiagnosticsState:
    """Store only non-secret telemetry from the last explicit live-view probe."""

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
    remote_disconnect_observed: bool = False
    connection_lost_exception_present: bool = False
    p2p_heartbeat_count: int = 0
    udp_network_bc_datagrams_received: int = 0
    udp_seq_gap_events: int = 0
    udp_recovered_missing_packet_count: int = 0
    udp_unresolved_missing_packet_count_at_disconnect: int = 0
    elapsed_seconds: float | None = None
    termination_reason: str = ""

    uid_resolve_timeout_seconds: float = 0.0
    uid_resolve_resend_interval_seconds: float = 0.0
    uid_resolve_send_rounds: int = 0
    uid_resolve_datagrams_sent: int = 0
    uid_resolve_elapsed_ms: float | None = None
    uid_resolve_succeeded: bool = False


_STATES: dict[str, LiveProbeDiagnosticsState] = {}


def live_probe_state(entry_id: str) -> LiveProbeDiagnosticsState:
    """Return the last secret-safe live probe state for one config entry."""
    return _STATES.setdefault(entry_id, LiveProbeDiagnosticsState())


def reset_live_probe_state(entry_id: str, *, stream_kind: str = "main") -> None:
    """Reset telemetry immediately before one explicit live-view attempt."""
    _STATES[entry_id] = LiveProbeDiagnosticsState(
        attempted=True,
        stream_kind=stream_kind,
    )


def _copy_uid_trace(state: LiveProbeDiagnosticsState, trace: Any) -> None:
    if trace is None:
        return
    state.uid_resolve_timeout_seconds = float(
        getattr(trace, "timeout_seconds", 0.0) or 0.0
    )
    state.uid_resolve_resend_interval_seconds = float(
        getattr(trace, "resend_interval_seconds", 0.0) or 0.0
    )
    state.uid_resolve_send_rounds = int(getattr(trace, "send_rounds", 0) or 0)
    state.uid_resolve_datagrams_sent = int(
        getattr(trace, "datagrams_sent", 0) or 0
    )
    state.uid_resolve_elapsed_ms = getattr(trace, "elapsed_ms", None)
    state.uid_resolve_succeeded = bool(getattr(trace, "succeeded", False))


def _copy_trace(state: LiveProbeDiagnosticsState, trace: Any) -> None:
    if trace is None:
        return
    state.stream_kind = str(getattr(trace, "stream_kind", state.stream_kind) or "main")
    state.start_attempted = bool(getattr(trace, "start_attempted", False))
    state.start_response_code = getattr(trace, "start_response_code", None)
    state.start_accepted = bool(getattr(trace, "start_accepted", False))
    state.first_cmd3_delay_ms = getattr(trace, "first_cmd3_delay_ms", None)
    state.cmd3_frames = int(getattr(trace, "cmd3_frames", 0) or 0)
    state.body_frames = int(getattr(trace, "body_frames", 0) or 0)
    state.total_body_bytes = int(getattr(trace, "total_body_bytes", 0) or 0)
    state.bcmedia_observed = bool(getattr(trace, "bcmedia_observed", False))
    state.bcmedia_info_frames = int(getattr(trace, "bcmedia_info_frames", 0) or 0)
    state.video_frames = int(getattr(trace, "video_frames", 0) or 0)
    state.iframe_frames = int(getattr(trace, "iframe_frames", 0) or 0)
    state.pframe_frames = int(getattr(trace, "pframe_frames", 0) or 0)
    state.h264_frames = int(getattr(trace, "h264_frames", 0) or 0)
    state.h265_frames = int(getattr(trace, "h265_frames", 0) or 0)
    state.unknown_body_frames = int(getattr(trace, "unknown_body_frames", 0) or 0)
    state.stop_attempted = bool(getattr(trace, "stop_attempted", False))
    state.stop_response_code = getattr(trace, "stop_response_code", None)
    state.stop_accepted = bool(getattr(trace, "stop_accepted", False))
    state.remote_disconnect_observed = bool(
        getattr(trace, "remote_disconnect_observed", False)
    )
    state.connection_lost_exception_present = bool(
        getattr(trace, "connection_lost_exception_present", False)
    )
    state.p2p_heartbeat_count = int(getattr(trace, "p2p_heartbeat_count", 0) or 0)
    state.udp_network_bc_datagrams_received = int(
        getattr(trace, "udp_network_bc_datagrams_received", 0) or 0
    )
    state.udp_seq_gap_events = int(getattr(trace, "udp_seq_gap_events", 0) or 0)
    state.udp_recovered_missing_packet_count = int(
        getattr(trace, "udp_recovered_missing_packet_count", 0) or 0
    )
    state.udp_unresolved_missing_packet_count_at_disconnect = int(
        getattr(trace, "udp_unresolved_missing_packet_count_at_disconnect", 0) or 0
    )
    state.elapsed_seconds = getattr(trace, "elapsed_seconds", None)
    state.termination_reason = str(getattr(trace, "termination_reason", "") or "")


def _copy_request(state: LiveProbeDiagnosticsState, request: Any, *, start: bool) -> None:
    if request is None:
        return
    prefix = "start_request" if start else "stop_request"
    setattr(state, f"{prefix}_header_channel_id", getattr(request, "header_channel_id", None))
    setattr(state, f"{prefix}_stream_type", getattr(request, "stream_type", None))
    setattr(state, f"{prefix}_msg_num", getattr(request, "msg_num", None))
    setattr(state, f"{prefix}_message_class", getattr(request, "message_class", None))
    setattr(state, f"{prefix}_body_length", getattr(request, "body_length", None))
    setattr(state, f"{prefix}_payload_offset", getattr(request, "payload_offset", None))
    setattr(state, f"{prefix}_preview_handle", getattr(request, "preview_handle", None))
    if start:
        state.start_request_preview_stream_type = getattr(
            request, "preview_stream_type", None
        )


def apply_live_probe_result(entry_id: str, result: Any) -> None:
    """Persist only explicitly selected non-secret fields from a successful probe."""
    state = live_probe_state(entry_id)
    state.attempted = True
    state.success = True
    state.failure_stage = ""
    state.failure_type = ""
    state.response_code = None
    _copy_request(state, getattr(result, "start_request", None), start=True)
    _copy_request(state, getattr(result, "stop_request", None), start=False)
    _copy_trace(state, getattr(result, "trace", None))
    _copy_uid_trace(state, getattr(result, "uid_resolve_trace", None))


def apply_live_probe_error(entry_id: str, error: Any) -> None:
    """Persist secret-safe failure telemetry without credentials or network IDs."""
    state = live_probe_state(entry_id)
    state.attempted = True
    state.success = False
    state.failure_stage = str(getattr(error, "stage", "") or "")
    state.failure_type = str(getattr(error, "failure_type", "") or type(error).__name__)
    response_code = getattr(error, "response_code", None)
    state.response_code = response_code if isinstance(response_code, int) else None
    _copy_trace(state, getattr(error, "trace", None))
    _copy_uid_trace(state, getattr(error, "uid_resolve_trace", None))
