"""Cloud-only event poller and persistent pending queue for Milestone 3A."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .cloud import CloudEventDecodeError, CloudTokens, ReolinkCloudClient
from .const import DEFAULT_EVENT_WINDOW, DEFAULT_POLL_INTERVAL, DOMAIN, STORAGE_VERSION
from .events import CloudEvent, EventQueue, parse_cloud_events

_LOGGER = logging.getLogger(__name__)


class ReolinkBatteryCoordinator:
    """Poll Message Center without contacting or waking the camera."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        cloud: ReolinkCloudClient,
        tokens: CloudTokens,
        email: str,
        account_password: str,
        uid: str,
        update_tokens: Callable[[CloudTokens], None],
    ) -> None:
        self._cloud = cloud
        self._tokens = tokens
        self._email = email
        self._account_password = account_password
        self._uid = uid
        self._update_tokens = update_tokens
        self._queue = EventQueue()
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}",
            private=True,
            atomic_writes=True,
        )
        self._stopped = asyncio.Event()
        self.last_successful_event_time: datetime | None = None
        self.last_failure_stage = ""
        self.last_failure_type = ""
        self.last_failure_reason = ""
        self.last_poll_time: datetime | None = None
        self.last_http_status: int | None = None
        self.last_response_wrapped: bool | None = None
        self.last_item_count = 0
        self.last_next_token_present: bool | None = None
        self.last_event_type = ""
        self.last_event_ai_types: tuple[str, ...] = ()
        self.last_event_id_present = False
        self.last_event_uid_match = False
        self.last_event_queued = False
        self.cloud_user_id_present = bool(tokens.user_id)

    @property
    def pending_events(self) -> tuple[CloudEvent, ...]:
        return self._queue.pending

    @property
    def processed_event_count(self) -> int:
        return self._queue.processed_count

    async def async_initialize(self) -> None:
        self._queue.load(await self._store.async_load())
        if self._queue.pending:
            self.last_successful_event_time = max(
                event.alarm_time for event in self._queue.pending
            )

    async def _async_ensure_session(self) -> None:
        now = datetime.now(UTC).timestamp()
        changed = False
        if self._tokens.access_token and (
            not self._tokens.expires_at or self._tokens.expires_at > now + 60
        ):
            pass
        elif self._tokens.refresh_token:
            self._tokens = await self._cloud.async_refresh_session(self._tokens)
            changed = True
        else:
            self._tokens = await self._cloud.async_password_grant(
                self._email, self._account_password, self._tokens.mfa_trust_token
            )
            changed = True
        if not self._tokens.user_id:
            self._tokens = CloudTokens(
                access_token=self._tokens.access_token,
                refresh_token=self._tokens.refresh_token,
                mfa_trust_token=self._tokens.mfa_trust_token,
                user_id=await self._cloud.async_query_user_id(
                    self._tokens.access_token
                ),
                expires_at=self._tokens.expires_at,
            )
            changed = True
        self.cloud_user_id_present = bool(self._tokens.user_id)
        if changed:
            self._update_tokens(self._tokens)

    async def async_ingest_events(self, events: list[CloudEvent]) -> int:
        """Add decoded events to the persistent 3A queue."""
        accepted = [event for event in events if self._queue.enqueue(event)]
        if accepted:
            self.last_successful_event_time = max(
                event.alarm_time for event in accepted
            )
            await self._store.async_save(self._queue.as_storage())
        return len(accepted)

    async def async_poll(self) -> int:
        """Poll one recent cloud-only window and enqueue new events."""
        now = datetime.now(UTC)
        self.last_poll_time = now
        self.last_event_queued = False
        _LOGGER.info("MESSAGE_CENTER_POLL_START")
        try:
            await self._async_ensure_session()
            page = await self._cloud.async_query_events(
                self._tokens.access_token,
                self._tokens.user_id,
                self._uid,
                now - DEFAULT_EVENT_WINDOW,
                now,
            )
            self.last_http_status = page.http_status
            self.last_response_wrapped = page.wrapped
            self.last_item_count = page.item_count
            self.last_next_token_present = page.next_token_present
            _LOGGER.info("MESSAGE_CENTER_HTTP_STATUS=%s", page.http_status)
            _LOGGER.info("MESSAGE_CENTER_WRAPPED=%s", int(page.wrapped))
            _LOGGER.info("MESSAGE_CENTER_ITEMS=%s", page.item_count)
            _LOGGER.info(
                "MESSAGE_CENTER_NEXT_TOKEN_PRESENT=%s",
                int(page.next_token_present),
            )
            events = parse_cloud_events(page.payload, self._uid)
            added = await self.async_ingest_events(events)
            if events:
                event = events[0]
                self.last_event_id_present = True
                self.last_event_uid_match = event.uid == self._uid
                self.last_event_type = event.alarm_type
                self.last_event_ai_types = event.ai_types
                self.last_event_queued = any(
                    pending.event_id == event.event_id
                    for pending in self._queue.pending
                )
                _LOGGER.info("EVENT_ID_PRESENT=1")
                _LOGGER.info("EVENT_UID_MATCH=%s", int(self.last_event_uid_match))
                _LOGGER.info("EVENT_ALARM_AT=%s", event.alarm_time.isoformat())
                _LOGGER.info("EVENT_ALARM_TYPE=%s", event.alarm_type)
                _LOGGER.info("EVENT_AI_TYPES=%s", ",".join(event.ai_types))
                _LOGGER.info("EVENT_QUEUED=%s", int(self.last_event_queued))
        except CloudEventDecodeError as err:
            self.last_http_status = err.status or None
            self.last_response_wrapped = err.wrapped
            self.last_failure_stage = "CLOUD_EVENT_ERROR"
            self.last_failure_type = type(err).__name__
            self.last_failure_reason = err.reason
            _LOGGER.debug("Cloud event decode failed: %s", err.reason)
            return 0
        except Exception as err:  # noqa: BLE001 - poller must survive cloud outages.
            self.last_failure_stage = "CLOUD_EVENT_ERROR"
            self.last_failure_type = type(err).__name__
            self.last_failure_reason = "cloud_request_failed"
            _LOGGER.debug("Cloud event poll failed: %s", type(err).__name__)
            return 0
        self.last_failure_stage = ""
        self.last_failure_type = ""
        self.last_failure_reason = ""
        return added

    async def async_run(self) -> None:
        """Run conservative cloud-only polling until config-entry unload."""
        while not self._stopped.is_set():
            await self.async_poll()
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), DEFAULT_POLL_INTERVAL.total_seconds()
                )
            except TimeoutError:
                pass

    async def async_shutdown(self) -> None:
        self._stopped.set()
        await self._store.async_save(self._queue.as_storage())
