"""Cloud-only event poller and persistent pending queue for Milestone 3A."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .cloud import CloudTokens, ReolinkCloudClient
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
        self.last_poll_time: datetime | None = None

    @property
    def pending_events(self) -> tuple[CloudEvent, ...]:
        return self._queue.pending

    @property
    def processed_event_count(self) -> int:
        return self._queue.processed_count

    async def async_initialize(self) -> None:
        self._queue.load(await self._store.async_load())

    async def _async_ensure_session(self) -> None:
        now = datetime.now(UTC).timestamp()
        if self._tokens.access_token and (
            not self._tokens.expires_at or self._tokens.expires_at > now + 60
        ):
            return
        if self._tokens.refresh_token:
            self._tokens = await self._cloud.async_refresh_session(self._tokens)
        else:
            self._tokens = await self._cloud.async_password_grant(
                self._email, self._account_password, self._tokens.mfa_trust_token
            )
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
        try:
            await self._async_ensure_session()
            payload = await self._cloud.async_query_events(
                self._tokens.access_token,
                self._uid,
                now - DEFAULT_EVENT_WINDOW,
                now,
            )
            added = await self.async_ingest_events(
                parse_cloud_events(payload, self._uid)
            )
        except Exception as err:  # noqa: BLE001 - poller must survive cloud outages.
            self.last_failure_stage = "CLOUD_EVENT_ERROR"
            _LOGGER.debug("Cloud event poll failed: %s", type(err).__name__)
            return 0
        self.last_failure_stage = ""
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
