"""Beta.22: persist the physically validated full-high MP4 atomically.

The camera protocol is unchanged from beta.21: proven cmd13 Id-only/no-Extension
prepare, returned handle, then cmd8 full-high/mainStream on the same authenticated
UDP/P2P session.  This beta adds only local file persistence.  MP4 bytes are
written to a private .part file as they arrive, fsynced, validated against the
camera-reported size and ISO-BMFF ftyp header, then atomically renamed to .mp4.
Incomplete or invalid .part files are removed.  No cmd9, automatic worker,
queue removal, or Telegram is added here.
"""

from __future__ import annotations

import copy
import hashlib
import os
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import recording_download_probe_beta17 as beta17
from . import recording_download_probe_beta21 as beta21

CONTENT_LAYOUT = "cmd13_prepare_cmd8_full_high_verified_atomic_mp4"
VERIFIED_FILE_OVERHEAD_BYTES = 4 * 1024 * 1024
VERIFIED_FILE_HARD_CAP_BYTES = 128 * 1024 * 1024
VERIFIED_FILE_MIN_FRAME_LIMIT = beta21.STREAM_SAMPLE_MAX_FRAMES


def _verified_collector_limits(expected_size: int) -> tuple[int, int]:
    """Return bounded collector limits sized for the selected recording.

    The 16 MiB limit belongs to the old diagnostic probe. Verified file
    persistence must be able to collect the complete camera-reported recording,
    while remaining bounded if metadata is corrupt.
    """
    expected = max(int(expected_size or 0), 0)
    byte_limit = max(
        beta21.STREAM_SAMPLE_MAX_BYTES,
        expected + VERIFIED_FILE_OVERHEAD_BYTES,
    )
    byte_limit = min(byte_limit, VERIFIED_FILE_HARD_CAP_BYTES)
    frame_limit = max(
        VERIFIED_FILE_MIN_FRAME_LIMIT,
        (byte_limit // 2048) + 1024,
    )
    return byte_limit, frame_limit


@dataclass(slots=True)
class VerifiedFileTrace(beta21.FullHighProbeTrace):
    """Secret-safe telemetry for verified local MP4 persistence."""

    file_write_attempted: bool = False
    part_created: bool = False
    file_bytes_written: int = 0
    fsync_completed: bool = False
    mp4_ftyp_valid: bool = False
    final_size: int = 0
    final_size_match: bool = False
    atomic_rename_completed: bool = False
    file_saved: bool = False
    part_removed_on_failure: bool = False
    sha256_present: bool = False
    output_private_path: bool = True
    telemetry_owner: str = "manual"
    telemetry_event_time: datetime | None = None
    single_lease_handoff: bool = False
    single_lease_socket_reused: bool = False
    single_lease_ids_reused: bool = False
    single_lease_transaction_id_reused: bool = False
    secondary_connect_sent: bool = False


_STREAM_STATES: dict[str, VerifiedFileTrace] = {}
_OUTPUT_DIR: ContextVar[str | None] = ContextVar(
    "reolink_battery_beta22_output_dir", default=None
)
_TELEMETRY_OWNER: ContextVar[str] = ContextVar(
    "reolink_battery_beta30_telemetry_owner", default="manual"
)
_TELEMETRY_EVENT_TIME: ContextVar[datetime | None] = ContextVar(
    "reolink_battery_beta30_telemetry_event_time", default=None
)


def _new_trace(
    *,
    attempted: bool = False,
    telemetry_owner: str | None = None,
    telemetry_event_time: datetime | None = None,
) -> VerifiedFileTrace:
    return VerifiedFileTrace(
        attempted=attempted,
        sample_limit_bytes=beta21.STREAM_SAMPLE_MAX_BYTES,
        sample_limit_frames=beta21.STREAM_SAMPLE_MAX_FRAMES,
        aggregate_limit_bytes=beta21.STREAM_SAMPLE_MAX_BYTES,
        keepalive_attempted=False,
        keepalive_count=0,
        keepalive_interval_seconds=0.0,
        telemetry_owner=telemetry_owner or _TELEMETRY_OWNER.get(),
        telemetry_event_time=(
            telemetry_event_time
            if telemetry_event_time is not None
            else _TELEMETRY_EVENT_TIME.get()
        ),
    )


def _clone_trace(trace: VerifiedFileTrace) -> VerifiedFileTrace:
    return copy.deepcopy(trace)


def stream_probe_state(entry_id: str) -> VerifiedFileTrace:
    return _STREAM_STATES.setdefault(entry_id, _new_trace())


def reset_stream_probe_state(
    entry_id: str,
    *,
    telemetry_owner: str = "manual",
    telemetry_event_time: datetime | None = None,
) -> None:
    _STREAM_STATES[entry_id] = _new_trace(
        attempted=True,
        telemetry_owner=telemetry_owner,
        telemetry_event_time=telemetry_event_time,
    )


def apply_stream_probe_trace(entry_id: str, trace: VerifiedFileTrace | None) -> None:
    if trace is not None:
        _STREAM_STATES[entry_id] = _clone_trace(trace)


def _valid_mp4_head(head: bytes) -> bool:
    if len(head) < 8 or head[4:8] != b"ftyp":
        return False
    size = int.from_bytes(head[:4], "big")
    return size == 1 or size >= 8


class _VerifiedFileConnection(beta21._FullHighCmd8Connection):
    """Beta.21 transport plus streaming .part persistence and atomic finalize."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_trace = _new_trace(attempted=True)
        self._output_dir: Path | None = None
        self._part_path: Path | None = None
        self._final_path: Path | None = None
        self._part_fh = None
        self._persisted_mp4_bytes = 0
        self._sha256 = hashlib.sha256()

    def prepare_full_high_candidate(
        self,
        encryptor,
        decryptor,
        candidate,
        cmd8_msg_num: int,
    ) -> None:
        super().prepare_full_high_candidate(
            encryptor, decryptor, candidate, cmd8_msg_num
        )
        self._stream_trace = _new_trace(attempted=True)
        collector_bytes, collector_frames = _verified_collector_limits(
            int(getattr(candidate, "size", 0) or 0)
        )
        self._stream_trace.sample_limit_bytes = collector_bytes
        self._stream_trace.aggregate_limit_bytes = collector_bytes
        self._stream_trace.sample_limit_frames = collector_frames
        self._stream_trace.single_lease_handoff = bool(
            getattr(self, "_handoff_mode", False)
        )
        self._stream_trace.single_lease_socket_reused = bool(
            getattr(self, "_handoff_active", False)
        )
        self._stream_trace.single_lease_ids_reused = (
            self._stream_trace.single_lease_socket_reused
            and getattr(self._protocol, "client_id", None) is not None
            and getattr(self._protocol, "host_id", None) is not None
        )
        self._stream_trace.single_lease_transaction_id_reused = (
            self._stream_trace.single_lease_socket_reused
            and getattr(self, "_handoff_transaction_id", None) is not None
            and getattr(self, "_p2p_heartbeat_tid", None)
            == getattr(self, "_handoff_transaction_id", None)
        )
        self._stream_trace.secondary_connect_sent = not (
            self._stream_trace.single_lease_handoff
            and self._stream_trace.single_lease_socket_reused
        )
        output_dir = _OUTPUT_DIR.get()
        if not output_dir:
            raise RuntimeError("beta22 private output directory not configured")
        self._output_dir = Path(output_dir)
        stamp = candidate.start_time.strftime("%Y%m%d_%H%M%S")
        self._final_path = self._output_dir / f"reolink_{stamp}.mp4"
        self._part_path = self._output_dir / f"reolink_{stamp}.mp4.part"
        self._part_fh = None
        self._persisted_mp4_bytes = 0
        self._sha256 = hashlib.sha256()

    def _open_part(self) -> None:
        if self._part_path is None or self._output_dir is None:
            raise RuntimeError("beta22 output path not prepared")
        trace = self._stream_trace
        trace.file_write_attempted = True
        self._output_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._part_path.unlink()
        except FileNotFoundError:
            pass
        self._part_fh = self._part_path.open("wb")
        trace.part_created = True

    def _persist_new_mp4_bytes(self) -> None:
        fh = self._part_fh
        trace = self._stream_trace
        if fh is None or trace.mp4_offset is None:
            return
        available = max(len(self._aggregate) - trace.mp4_offset, 0)
        if available <= self._persisted_mp4_bytes:
            return
        start = trace.mp4_offset + self._persisted_mp4_bytes
        end = trace.mp4_offset + available
        data = bytes(self._aggregate[start:end])
        if not data:
            return
        fh.write(data)
        self._sha256.update(data)
        self._persisted_mp4_bytes += len(data)
        trace.file_bytes_written = self._persisted_mp4_bytes

    def _observe_frame(self, frame: beta17._RawDownloadFrame) -> None:
        super()._observe_frame(frame)
        if frame.cmd_id == 8 and frame.response_code in (0, 200):
            self._persist_new_mp4_bytes()

    def _remove_part(self) -> None:
        path = self._part_path
        if path is None:
            return
        try:
            path.unlink()
            self._stream_trace.part_removed_on_failure = True
        except FileNotFoundError:
            pass

    def _close_part(self) -> None:
        fh = self._part_fh
        self._part_fh = None
        if fh is not None and not fh.closed:
            fh.close()

    def _finalize_verified_file(self) -> None:
        trace = self._stream_trace
        fh = self._part_fh
        part = self._part_path
        final = self._final_path
        if fh is None or part is None or final is None:
            raise RuntimeError("beta22 part file not open")

        self._persist_new_mp4_bytes()
        fh.flush()
        os.fsync(fh.fileno())
        trace.fsync_completed = True
        self._close_part()

        trace.final_size = part.stat().st_size
        with part.open("rb") as check:
            head = check.read(32)
        trace.mp4_ftyp_valid = _valid_mp4_head(head)
        expected = trace.xml_reported_size
        trace.final_size_match = (
            expected is not None
            and expected > 0
            and trace.final_size == expected
            and trace.file_bytes_written == expected
        )
        trace.sha256_present = trace.final_size > 0

        if not (
            trace.expected_size_match
            and trace.final_size_match
            and trace.mp4_ftyp_valid
        ):
            self._remove_part()
            return

        os.replace(part, final)
        trace.atomic_rename_completed = True
        try:
            dir_fd = os.open(str(final.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # File fsync and atomic rename already completed. Directory fsync is
            # best-effort on filesystems that do not support opening directories.
            pass
        trace.file_saved = final.is_file() and final.stat().st_size == expected

    async def send_file_download_probe(
        self,
        wire: bytes,
        *,
        expected_msg_num: int,
        timeout: float = 10.0,
    ):
        self._open_part()
        try:
            first = await super().send_file_download_probe(
                wire, expected_msg_num=expected_msg_num, timeout=timeout
            )
            self._finalize_verified_file()
            beta21._RESULT_TRACE.set(_clone_trace(self._stream_trace))
            return first
        except BaseException:
            self._close_part()
            self._remove_part()
            beta21._RESULT_TRACE.set(_clone_trace(self._stream_trace))
            raise
        finally:
            self._close_part()


async def async_prepare_download_for_event(
    *args,
    output_dir: str | None = None,
    telemetry_owner: str = "manual",
    telemetry_event_time: datetime | None = None,
    **kwargs,
):
    """Run beta.21 protocol and persist a verified MP4 to a private directory."""
    if not output_dir:
        raise RuntimeError("beta22 output_dir is required")
    output_token = _OUTPUT_DIR.set(output_dir)
    owner_token = _TELEMETRY_OWNER.set(telemetry_owner)
    event_token = _TELEMETRY_EVENT_TIME.set(telemetry_event_time)
    try:
        return await beta21.async_prepare_download_for_event(*args, **kwargs)
    finally:
        _TELEMETRY_EVENT_TIME.reset(event_token)
        _TELEMETRY_OWNER.reset(owner_token)
        _OUTPUT_DIR.reset(output_token)


# Beta.21's cmd13 builder recognizes subclasses of _FullHighCmd8Connection and
# therefore prepares this connection with the same proven full-high cmd8 data.
beta21.base.BoundBaichuanUdpConnection = _VerifiedFileConnection
beta21.base._build_cmd13_wire = beta21._build_cmd13_wire

apply_file_info_trace = beta21.apply_file_info_trace
apply_identity_trace = beta21.apply_identity_trace
download_prepare_state = beta21.download_prepare_state
