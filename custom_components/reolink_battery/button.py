"""Manual battery-safe camera actions."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo

from . import (
    LocalStatusRefreshError,
    ReolinkBatteryConfigEntry,
    async_refresh_local_status,
)
from .camera import CameraStageError
from .const import (
    CONF_DEVICE_PASSWORD,
    CONF_DEVICE_USERNAME,
    CONF_INTERFACE,
    CONF_UID,
    DOMAIN,
)
from .recording_download_probe import (
    async_prepare_download_for_event,
    download_prepare_state,
)
from .recording_probe import async_find_recording_for_event, probe_state

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


REFRESH_DESCRIPTION = ButtonEntityDescription(
    key="refresh_device_status",
    translation_key="refresh_device_status",
)
FIND_RECORDING_DESCRIPTION = ButtonEntityDescription(
    key="find_pending_recording",
    translation_key="find_pending_recording",
)
PREPARE_DOWNLOAD_DESCRIPTION = ButtonEntityDescription(
    key="prepare_pending_recording_download",
    translation_key="prepare_pending_recording_download",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReolinkBatteryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add explicit short-session buttons."""
    async_add_entities(
        (
            ReolinkRefreshDeviceStatusButton(entry),
            ReolinkFindPendingRecordingButton(entry),
            ReolinkPreparePendingRecordingDownloadButton(entry),
        )
    )


class ReolinkRefreshDeviceStatusButton(ButtonEntity):
    """Start one short status-only camera session on explicit press."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description = REFRESH_DESCRIPTION

    def __init__(self, entry: ReolinkBatteryConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_UID]}_refresh_device_status"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data[CONF_UID])})

    async def async_press(self) -> None:
        """Refresh cached local status once."""
        try:
            await async_refresh_local_status(self.hass, self._entry)
        except LocalStatusRefreshError as err:
            raise HomeAssistantError(err.stage) from None


class ReolinkFindPendingRecordingButton(ButtonEntity):
    """Explicitly find the SD recording nearest the oldest pending phone event."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description = FIND_RECORDING_DESCRIPTION

    def __init__(self, entry: ReolinkBatteryConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_UID]}_find_pending_recording"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data[CONF_UID])})

    @property
    def available(self) -> bool:
        return any(
            event.source == "android_notification"
            for event in self._entry.runtime_data.coordinator.pending_events
        )

    async def async_press(self) -> None:
        """Wake only on this explicit press, search one day, and close immediately."""
        event = next(
            (
                item
                for item in self._entry.runtime_data.coordinator.pending_events
                if item.source == "android_notification"
            ),
            None,
        )
        if event is None:
            raise HomeAssistantError("NO_PENDING_NOTIFICATION_EVENT")

        state = probe_state(self._entry.entry_id)
        state.attempted = True
        state.success = False
        state.event_time = event.notification_post_time or event.alarm_time
        state.candidate_start = None
        state.candidate_end = None
        state.candidate_size = None
        state.candidate_distance_seconds = None
        state.candidate_name_present = False
        state.failure_stage = ""

        try:
            async with self._entry.runtime_data.local_operation_lock:
                candidate = await async_find_recording_for_event(
                    event,
                    self._entry.data[CONF_UID],
                    self._entry.data[CONF_DEVICE_USERNAME],
                    self._entry.data[CONF_DEVICE_PASSWORD],
                    ipaddress.ip_interface(self._entry.data[CONF_INTERFACE]),
                    self.hass.config.time_zone,
                )
        except CameraStageError as err:
            state.failure_stage = err.stage
            raise HomeAssistantError(err.stage) from None

        state.success = True
        state.candidate_start = candidate.start_time
        state.candidate_end = candidate.end_time
        state.candidate_size = candidate.size
        state.candidate_distance_seconds = candidate.distance_seconds
        state.candidate_name_present = bool(candidate.file_name)


class ReolinkPreparePendingRecordingDownloadButton(ButtonEntity):
    """Probe cmd13 routing for the pending recording without cmd8."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description = PREPARE_DOWNLOAD_DESCRIPTION

    def __init__(self, entry: ReolinkBatteryConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = (
            f"{entry.data[CONF_UID]}_prepare_pending_recording_download"
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data[CONF_UID])})

    @property
    def available(self) -> bool:
        return any(
            event.source == "android_notification"
            for event in self._entry.runtime_data.coordinator.pending_events
        )

    async def async_press(self) -> None:
        """Find the queued recording, issue one cmd13 routing probe, then close."""
        event = next(
            (
                item
                for item in self._entry.runtime_data.coordinator.pending_events
                if item.source == "android_notification"
            ),
            None,
        )
        if event is None:
            raise HomeAssistantError("NO_PENDING_NOTIFICATION_EVENT")

        state = download_prepare_state(self._entry.entry_id)
        state.attempted = True
        state.success = False
        state.event_time = event.notification_post_time or event.alarm_time
        state.candidate_start = None
        state.candidate_end = None
        state.candidate_distance_seconds = None
        state.response_present = False
        state.response_accepted = False
        state.failure_stage = ""
        state.failure_type = ""
        state.response_code = None
        state.request_header_channel_id = None
        state.request_message_id = None
        state.request_full_message_id = None
        state.request_message_class = None
        state.request_body_length = None
        state.request_payload_offset = None
        state.response_message_class = None
        state.response_header_channel_id = None
        state.response_message_id = None
        state.response_full_message_id = None
        state.response_body_length = None
        state.response_payload_offset = None
        state.first_payload_length = 0

        try:
            async with self._entry.runtime_data.local_operation_lock:
                result = await async_prepare_download_for_event(
                    event,
                    self._entry.data[CONF_UID],
                    self._entry.data[CONF_DEVICE_USERNAME],
                    self._entry.data[CONF_DEVICE_PASSWORD],
                    ipaddress.ip_interface(self._entry.data[CONF_INTERFACE]),
                    self.hass.config.time_zone,
                )
        except CameraStageError as err:
            state.failure_stage = err.stage
            state.failure_type = getattr(err, "failure_type", "")
            response_code = getattr(err, "response_code", None)
            state.response_code = response_code if isinstance(response_code, int) else None
            raise HomeAssistantError(err.stage) from None

        state.candidate_start = result.candidate_start
        state.candidate_end = result.candidate_end
        state.candidate_distance_seconds = result.candidate_distance_seconds
        state.response_present = True
        state.response_accepted = result.response_accepted
        state.response_code = result.response.response_code
        state.request_header_channel_id = result.request.header_channel_id
        state.request_message_id = result.request.message_id
        state.request_full_message_id = result.request.full_message_id
        state.request_message_class = result.request.message_class
        state.request_body_length = result.request.body_length
        state.request_payload_offset = result.request.payload_offset
        state.response_message_class = result.response.message_class
        state.response_header_channel_id = result.response.header_channel_id
        state.response_message_id = result.response.message_id
        state.response_full_message_id = result.response.full_message_id
        state.response_body_length = result.response.body_length
        state.response_payload_offset = result.response.payload_offset
        state.first_payload_length = result.response.payload_length
        state.success = result.response_accepted

        if not result.response_accepted:
            state.failure_stage = (
                f"DOWNLOAD_PREPARE_RESPONSE_{result.response.response_code}"
            )
            raise HomeAssistantError(state.failure_stage)
