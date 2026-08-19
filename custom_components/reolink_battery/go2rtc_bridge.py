"""Best-effort automatic go2rtc provisioning for Reolink Battery Live View."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .live_http import aac_path, h264_path

GO2RTC_API_PORT = 1984
GO2RTC_RTSP_PORT = 8554


@dataclass(slots=True)
class Go2RtcBridgeState:
    """Non-secret state of one configured go2rtc stream."""

    attempted: bool = False
    success: bool = False
    stream_name: str = ""
    rtsp_url: str | None = None
    http_status: int | None = None
    failure_type: str | None = None
    sources_registered: int = 0


def _host_for_url(hostname: str) -> str:
    return f"[{hostname}]" if ":" in hostname else hostname


def _stream_name(entry) -> str:
    # entry_id is local metadata, stable and contains no camera credentials.
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", entry.entry_id)
    return f"reolink_battery_{safe}"


def _derive_urls(hass: HomeAssistant, stream_name: str) -> tuple[str, str, str]:
    instance_url = get_url(
        hass,
        allow_internal=True,
        allow_external=False,
        allow_cloud=False,
        allow_ip=True,
        prefer_external=False,
    ).rstrip("/")
    parsed = urlsplit(instance_url)
    if not parsed.hostname:
        raise ValueError("Home Assistant internal URL has no hostname")
    host = _host_for_url(parsed.hostname)
    api_url = f"http://{host}:{GO2RTC_API_PORT}"
    rtsp_url = (
        f"rtsp://{host}:{GO2RTC_RTSP_PORT}/{stream_name}"
        "?video=h264&audio=aac"
    )
    return instance_url, api_url, rtsp_url


async def async_ensure_go2rtc_bridge(hass: HomeAssistant, entry) -> Go2RtcBridgeState:
    """Persist H264 + AAC + WebRTC Opus sources in go2rtc.

    This only edits go2rtc configuration. It does not connect to or wake the
    battery camera; go2rtc opens the raw HTTP endpoints only when a consumer
    requests the stream.
    """
    stream_name = _stream_name(entry)
    state = Go2RtcBridgeState(attempted=True, stream_name=stream_name)
    try:
        ha_base, api_url, rtsp_url = _derive_urls(hass, stream_name)
    except (NoURLAvailableError, ValueError) as err:
        state.failure_type = type(err).__name__
        return state

    state.rtsp_url = rtsp_url
    h264_source = f"{ha_base}{h264_path(entry.entry_id)}"
    aac_source = f"{ha_base}{aac_path(entry.entry_id)}"
    opus_source = f"ffmpeg:{aac_source}#audio=opus"
    sources = (h264_source, aac_source, opus_source)
    params: list[tuple[str, str]] = [("name", stream_name)]
    params.extend(("src", source) for source in sources)

    try:
        session = async_get_clientsession(hass)
        async with session.put(
            f"{api_url}/api/streams",
            params=params,
            timeout=ClientTimeout(total=8),
        ) as response:
            state.http_status = response.status
            if 200 <= response.status < 300:
                state.success = True
                state.sources_registered = len(sources)
            else:
                state.failure_type = f"HTTP_{response.status}"
    except (ClientError, TimeoutError, OSError) as err:
        state.failure_type = type(err).__name__

    return state
