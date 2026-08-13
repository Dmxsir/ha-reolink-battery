"""Secret-safe diagnostics for Reolink Battery."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import ReolinkBatteryConfigEntry
from .const import CONF_AUTH_PATH, CONF_MODEL, CONF_UID


def _redacted_uid(uid: str) -> str:
    if len(uid) <= 6:
        return "***"
    return f"{uid[:3]}…{uid[-3:]}"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ReolinkBatteryConfigEntry
) -> dict[str, Any]:
    """Return no credentials, tokens, network addresses, or session material."""
    coordinator = entry.runtime_data.coordinator
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
        },
        "milestone": "3A",
        "camera_worker_enabled": False,
    }
