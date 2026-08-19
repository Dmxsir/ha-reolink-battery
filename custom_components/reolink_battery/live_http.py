"""Local-only H264/AAC endpoints backed by one on-demand Argus session."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from contextlib import suppress
from http import HTTPStatus

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView

from .const import (
    CONF_AUTH_PATH,
    CONF_DEVICE_PASSWORD,
    CONF_DEVICE_USERNAME,
    CONF_INTERFACE,
    CONF_MODEL,
    CONF_UID,
    DOMAIN,
)
from .live_stream import LiveStreamTrace, async_stream_media

_LOGGER = logging.getLogger(__name__)

HTTP_H264_PATH = "/api/reolink_battery/{entry_id}/main.h264"
HTTP_AAC_PATH = "/api/reolink_battery/{entry_id}/main.aac"
_MAX_VIDEO_QUEUE = 64
_MAX_AUDIO_QUEUE = 512
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
ISSUE_COPY_MARKER = "### COPY THIS BLOCK TO GITHUB ISSUE ###"


def h264_path(entry_id: str) -> str:
    return HTTP_H264_PATH.format(entry_id=entry_id)


def aac_path(entry_id: str) -> str:
    return HTTP_AAC_PATH.format(entry_id=entry_id)


def _local_request(remote: str | None) -> bool:
    if not remote:
        return False
    try:
        address = ipaddress.ip_address(remote)
    except ValueError:
        return False
    if address.is_loopback or address.is_private:
        return True
    return isinstance(address, ipaddress.IPv4Address) and address in _CGNAT


def _redacted_uid(uid: str) -> str:
    if len(uid) <= 6:
        return "***"
    return f"{uid[:3]}…{uid[-3:]}"


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


class ReolinkBatteryLiveHub:
    """Fan out one battery-camera session to all H264/AAC consumers."""

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._guard = asyncio.Lock()
        self._video_queues: set[asyncio.Queue[bytes | None]] = set()
        self._audio_queues: set[asyncio.Queue[bytes | None]] = set()
        self._producer_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._sessions_started = 0
        self._sessions_completed = 0
        self._last_failure_stage: str | None = None
        self._last_failure_type: str | None = None
        self._last_trace: LiveStreamTrace | None = None

    @property
    def is_active(self) -> bool:
        task = self._producer_task
        return task is not None and not task.done()

    def _live_state(self) -> dict[str, object]:
        """Return secret-safe Live View runtime telemetry."""
        trace = self._last_trace
        return {
            "active": self.is_active,
            "video_consumers": len(self._video_queues),
            "audio_consumers": len(self._audio_queues),
            "sessions_started": self._sessions_started,
            "sessions_completed": self._sessions_completed,
            "last_failure_stage": self._last_failure_stage,
            "last_failure_type": self._last_failure_type,
            "last_session": (
                {
                    "start_accepted": trace.start_accepted,
                    "start_response_code": trace.start_response_code,
                    "stop_accepted": trace.stop_accepted,
                    "stop_response_code": trace.stop_response_code,
                    "h264_frames": trace.h264_frames,
                    "aac_packets": trace.aac_packets,
                    "p2p_heartbeat_count": trace.p2p_heartbeat_count,
                    "media_keepalive_count": trace.media_keepalive_count,
                    "termination_reason": trace.termination_reason or None,
                }
                if trace is not None
                else None
            ),
        }

    def _issue_report_snapshot(self, live_state: dict[str, object]) -> dict[str, object]:
        """Build one compact, copy-ready, secret-safe GitHub issue snapshot."""
        runtime = self.entry.runtime_data
        if runtime is None:
            return {
                "diagnostics_schema": 1,
                "runtime_available": False,
            }

        coordinator = runtime.coordinator
        bridge = runtime.notification_bridge
        worker = runtime.recording_worker
        go2rtc = runtime.go2rtc_bridge

        # These modules are already used by diagnostics.py. Keep the import lazy
        # here so ordinary Live View startup does not initialize recording probes.
        from .recording_download_beta22 import stream_probe_state

        stream = stream_probe_state(self.entry.entry_id)
        worker_state = worker.state if worker is not None else None

        return {
            "diagnostics_schema": 1,
            "device": {
                "model": self.entry.data.get(CONF_MODEL, ""),
                "auth_path": self.entry.data.get(CONF_AUTH_PATH, ""),
                "uid": _redacted_uid(self.entry.data.get(CONF_UID, "")),
            },
            "events": {
                "last_successful_event_time": _iso(coordinator.last_successful_event_time),
                "pending_count": len(coordinator.pending_events),
                "processed_count": coordinator.processed_event_count,
                "completed_recording_count": coordinator.completed_recording_count,
                "last_failure_stage": coordinator.last_failure_stage or None,
                "last_failure_type": coordinator.last_failure_type or None,
            },
            "notification_bridge": {
                "configured": bridge is not None,
                "listener_active": bool(bridge and bridge.active),
                "last_reolink_notification_time": _iso(
                    bridge.last_reolink_notification_time if bridge else None
                ),
                "last_event_matched": bool(bridge and bridge.last_event_matched),
                "last_event_queued": bool(bridge and bridge.last_event_queued),
                "duplicate_rejected": bool(
                    bridge and bridge.last_duplicate_rejected
                ),
                "last_processing_lag_seconds": (
                    bridge.last_processing_lag_seconds if bridge else None
                ),
                "stale_repost_promoted_count": (
                    getattr(bridge, "stale_repost_promoted_count", 0)
                    if bridge else 0
                ),
                "stale_repost_suppressed_count": (
                    getattr(bridge, "stale_repost_suppressed_count", 0)
                    if bridge else 0
                ),
                "last_stale_repost_promoted": bool(
                    bridge and getattr(bridge, "last_stale_repost_promoted", False)
                ),
                "last_effective_event_time": _iso(
                    getattr(bridge, "last_effective_event_time", None)
                    if bridge else None
                ),
                "last_effective_event_time_source": (
                    getattr(bridge, "last_effective_event_time_source", "")
                    if bridge else ""
                ),
            },
            "recording_worker": {
                "configured": worker is not None,
                "enabled": bool(worker_state and worker_state.enabled),
                "running": bool(worker_state and worker_state.running),
                "waiting_camera_closed": bool(
                    worker_state and worker_state.waiting_camera_closed
                ),
                "attempts": worker_state.attempts if worker_state else 0,
                "retries": worker_state.retries if worker_state else 0,
                "completed": worker_state.completed if worker_state else 0,
                "last_event_time": _iso(
                    worker_state.last_event_time if worker_state else None
                ),
                "last_attempt_time": _iso(
                    worker_state.last_attempt_time if worker_state else None
                ),
                "last_completed_time": _iso(
                    worker_state.last_completed_time if worker_state else None
                ),
                "last_failure_stage": (
                    worker_state.last_failure_stage or None
                    if worker_state else None
                ),
                "last_failure_type": (
                    worker_state.last_failure_type or None
                    if worker_state else None
                ),
                "last_file_saved": bool(
                    worker_state and worker_state.last_file_saved
                ),
                "last_file_size": worker_state.last_file_size if worker_state else 0,
                "uid_resolve_succeeded": bool(
                    worker_state and worker_state.last_uid_resolve_succeeded
                ),
                "uid_resolve_elapsed_ms": (
                    worker_state.last_uid_resolve_elapsed_ms
                    if worker_state else None
                ),
            },
            "recording_download": {
                "attempted": stream.attempted,
                "cmd13_frames": stream.cmd13_frames,
                "cmd8_attempted": stream.cmd8_attempted,
                "cmd8_frames": stream.cmd8_frames,
                "cmd8_media_observed": stream.cmd8_media_observed,
                "file_write_attempted": stream.file_write_attempted,
                "file_bytes_written": stream.file_bytes_written,
                "file_saved": stream.file_saved,
                "final_size": stream.final_size,
                "final_size_match": stream.final_size_match,
                "termination_reason": stream.termination_reason or None,
                "elapsed_seconds": stream.elapsed_seconds,
                "response_codes": list(stream.response_codes),
                "udp_bc_packets_received": stream.udp_bc_packets_received,
                "udp_missing_packet_count": stream.udp_missing_packet_count,
                "udp_recovered_missing_packet_count": (
                    stream.udp_recovered_missing_packet_count
                ),
                "udp_unresolved_missing_packet_count_at_disconnect": (
                    stream.udp_unresolved_missing_packet_count_at_disconnect
                ),
                "remote_disconnect_observed": stream.remote_disconnect_observed,
            },
            "live_feed": live_state,
            "go2rtc": {
                "attempted": bool(go2rtc and go2rtc.attempted),
                "success": bool(go2rtc and go2rtc.success),
                "http_status": go2rtc.http_status if go2rtc else None,
                "failure_type": go2rtc.failure_type if go2rtc else None,
                "sources_registered": go2rtc.sources_registered if go2rtc else 0,
                "stream_name_present": bool(go2rtc and go2rtc.stream_name),
                "rtsp_url_present": bool(go2rtc and go2rtc.rtsp_url),
            },
            "privacy": {
                "credentials_exposed": False,
                "network_addresses_exposed": False,
                "raw_media_exposed": False,
                "recording_filenames_exposed": False,
            },
        }

    def diagnostics_snapshot(self) -> dict[str, object]:
        """Return secret-safe runtime telemetry plus a compact issue block."""
        live_state = self._live_state()
        return {
            ISSUE_COPY_MARKER: self._issue_report_snapshot(live_state),
            **live_state,
            "raw_media_exposed": False,
            "network_identifiers_exposed": False,
        }

    async def subscribe(self, kind: str) -> asyncio.Queue[bytes | None]:
        maxsize = _MAX_VIDEO_QUEUE if kind == "video" else _MAX_AUDIO_QUEUE
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=maxsize)
        async with self._guard:
            target = self._video_queues if kind == "video" else self._audio_queues
            target.add(queue)
            if self._producer_task is None or self._producer_task.done():
                self._stop_event = asyncio.Event()
                self._producer_task = asyncio.create_task(self._run_source())
        return queue

    async def unsubscribe(self, kind: str, queue: asyncio.Queue[bytes | None]) -> None:
        async with self._guard:
            target = self._video_queues if kind == "video" else self._audio_queues
            target.discard(queue)
            if not self._video_queues and not self._audio_queues and self._stop_event is not None:
                self._stop_event.set()

    def _fanout(self, queues: set[asyncio.Queue[bytes | None]], payload: bytes) -> None:
        for queue in tuple(queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Never silently corrupt an inter-frame H264 stream or A/V sync.
                # End the session and let go2rtc reconnect from a fresh keyframe.
                if self._stop_event is not None:
                    self._stop_event.set()
                return

    def _video_sink(self, payload: bytes, _frame_type: str) -> None:
        self._fanout(self._video_queues, payload)

    def _audio_sink(self, payload: bytes, codec: str) -> None:
        # Hardware validation proved Argus 2E sends AAC with ADTS (FF F1).
        if codec == "aac":
            self._fanout(self._audio_queues, payload)

    async def _run_source(self) -> None:
        stop_event = self._stop_event
        runtime = self.entry.runtime_data
        if stop_event is None or runtime is None:
            await self._finish_producer()
            return

        required = (
            CONF_UID,
            CONF_DEVICE_USERNAME,
            CONF_DEVICE_PASSWORD,
            CONF_INTERFACE,
        )
        if any(not self.entry.data.get(key) for key in required):
            self._last_failure_stage = "LIVE_CONFIGURATION_INCOMPLETE"
            self._last_failure_type = None
            await self._finish_producer()
            return

        self._sessions_started += 1
        self._last_failure_stage = None
        self._last_failure_type = None
        try:
            async with runtime.local_operation_lock:
                self._last_trace = await async_stream_media(
                    self.entry.data[CONF_UID],
                    self.entry.data[CONF_DEVICE_USERNAME],
                    self.entry.data[CONF_DEVICE_PASSWORD],
                    ipaddress.ip_interface(self.entry.data[CONF_INTERFACE]),
                    h264_sink=self._video_sink,
                    audio_sink=self._audio_sink,
                    stop_event=stop_event,
                )
        except Exception as err:  # secret-safe: log type/stage only
            stage = getattr(err, "stage", type(err).__name__)
            failure_type = getattr(err, "failure_type", "") or type(err).__name__
            self._last_failure_stage = str(stage)
            self._last_failure_type = str(failure_type)
            _LOGGER.debug("Live source ended at %s", stage)
        finally:
            self._sessions_completed += 1
            await self._finish_producer()

    async def _finish_producer(self) -> None:
        async with self._guard:
            for queue in tuple(self._video_queues) + tuple(self._audio_queues):
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
            self._producer_task = None
            self._stop_event = None

    async def async_stop(self) -> None:
        async with self._guard:
            task = self._producer_task
            if self._stop_event is not None:
                self._stop_event.set()
        if task is not None and not task.done():
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


class _ReolinkBatteryMediaView(HomeAssistantView):
    """Serve one raw track to a local go2rtc consumer."""

    requires_auth = False
    media_kind = "video"
    content_type_value = "application/octet-stream"

    async def get(self, request: web.Request, entry_id: str) -> web.StreamResponse:
        if not _local_request(request.remote):
            raise web.HTTPForbidden()

        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.runtime_data is None:
            raise web.HTTPNotFound()

        hub = getattr(entry.runtime_data, "live_hub", None)
        if not isinstance(hub, ReolinkBatteryLiveHub):
            raise web.HTTPServiceUnavailable(text="Live bridge is unavailable")

        response = web.StreamResponse(
            status=HTTPStatus.OK,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )
        response.content_type = self.content_type_value
        await response.prepare(request)

        queue = await hub.subscribe(self.media_kind)
        try:
            while True:
                payload = await queue.get()
                if payload is None:
                    break
                await response.write(payload)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            await hub.unsubscribe(self.media_kind, queue)
            with suppress(ConnectionResetError, RuntimeError):
                await response.write_eof()
        return response


class ReolinkBatteryH264View(_ReolinkBatteryMediaView):
    url = HTTP_H264_PATH
    name = "api:reolink_battery:h264"
    media_kind = "video"
    content_type_value = "video/h264"


class ReolinkBatteryAacView(_ReolinkBatteryMediaView):
    url = HTTP_AAC_PATH
    name = "api:reolink_battery:aac"
    media_kind = "audio"
    content_type_value = "audio/aac"
