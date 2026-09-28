"""Following VB365's event feed, so job changes show up in seconds rather than on the next poll.

``/v8/Events`` is a change feed: each request names the change token the previous one returned
and the server holds it open for up to ``timeoutSeconds`` until something happens. The events
are thin — an event type and an ID, and for a job session its job and status — so the feed
does not replace polling. It tells the coordinator *when* to poll, and which job sessions to
re-read:

* any job or job session event requests a refresh (debounced by the coordinator);
* job session events name the sessions the next poll should read by ID, instead of listing;
* a job session whose status changes also fires a ``veeam_365_job_session`` event on the Home
  Assistant bus, for automations that want to react to it directly.

The regular poll carries on regardless. Whenever the feed fails — the server does not have it,
the token expired, the connection dropped — the listener backs off and starts again from the
latest token, and the coordinator falls back to listing job sessions until the feed has been
back for a whole poll. Events that happened while nothing was listening (Home Assistant was
down, the feed was failing) are therefore never needed: the listing resyncs.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from veeam_365.exceptions import VeeamError, VeeamSessionError

from .const import (
    EVENT_JOB_SESSION,
    EVENTS_RETRY_MAX_SECONDS,
    EVENTS_RETRY_MIN_SECONDS,
    EVENTS_WAIT_SECONDS,
    PAGE_LIMIT,
)
from .coordinator import (
    PARSE_ERRORS,
    TRANSPORT_ERRORS,
    EndpointError,
    describe_error,
    error_message,
    field,
    id_field,
    is_error_response,
    text_field,
)

if TYPE_CHECKING:
    from .coordinator import VeeamCoordinator
    from .sdk import VeeamSdk

_LOGGER = logging.getLogger(__name__)

EVENTS_OPERATION = "events.events_get"

# Events about what the regular poll shows: they request a refresh
REFRESH_EVENT_TYPES = frozenset(
    {
        "JobUpdate",
        "JobDelete",
        "JobSessionUpdate",
        "JobSessionDelete",
        "JobSelectedItemsChange",
        "JobExcludedItemsChange",
    }
)

# How many sessions' last status is remembered, to fire the bus event on changes only
STATUS_MEMORY = 1000


def supports_event_feed(sdk: VeeamSdk) -> bool:
    return sdk.has_operation(EVENTS_OPERATION)


class VeeamEventListener:
    """Follows the event feed for one config entry until it is unloaded."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: VeeamCoordinator):
        self.hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._statuses: OrderedDict[str, str | None] = OrderedDict()
        # For diagnostics
        self.connected = False
        self.last_success: Any = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        self.events_seen = 0

    def diagnostics(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "events_seen": self.events_seen,
        }

    async def run(self) -> None:
        """Listen until cancelled, which unloading the entry does."""
        token: str | None = None
        while True:
            try:
                response = await self._next(token)
            except asyncio.CancelledError:
                raise
            except VeeamSessionError as err:
                # The SDK has dropped the session and logs in again on the next call; the
                # token is still good, so carry on from it after a moment
                self._failed(err, reset=False)
                await asyncio.sleep(1)
                continue
            except (EndpointError, VeeamError, *TRANSPORT_ERRORS, *PARSE_ERRORS) as err:
                self._failed(err, reset=True)
                token = None
                await asyncio.sleep(self._backoff())
                continue

            next_token = text_field(response, "next_change_token")
            if not next_token:
                self._failed(EndpointError("the server returned no change token"), reset=True)
                token = None
                await asyncio.sleep(self._backoff())
                continue

            if self.consecutive_failures:
                _LOGGER.info(
                    "Veeam event feed is back after %d failures", self.consecutive_failures
                )
            self.consecutive_failures = 0
            self.connected = True
            self.last_success = dt_util.utcnow()
            self.last_error = None

            if token is None:
                # A fresh start from the latest token: whatever happened before it is not in
                # the feed, so the next poll lists job sessions rather than trusting it
                self._coordinator.event_feed_started()
            else:
                await self._handle(field(response, "results") or [])
            self._coordinator.event_feed_ok()
            token = next_token

    async def _next(self, token: str | None) -> Any:
        client = self._coordinator.client
        operation = self._coordinator.sdk.operation(EVENTS_OPERATION)
        # A little longer than the server is asked to wait, so a feed that goes quiet without
        # closing the request still comes back round
        async with asyncio.timeout(EVENTS_WAIT_SECONDS + 15):
            response = await client.call(
                operation,
                from_=token or "latest",
                limit=PAGE_LIMIT,
                timeout_seconds=EVENTS_WAIT_SECONDS,
            )
        if response is None:
            raise EndpointError("the server returned no data")
        if is_error_response(response):
            raise EndpointError(error_message(response))
        return response

    def _failed(self, err: BaseException, reset: bool) -> None:
        self.consecutive_failures += 1
        self.connected = False
        self.last_error = describe_error(err)
        if reset:
            self._coordinator.event_feed_failed()
        # Loud once, then quiet: a server without the feed would otherwise log forever
        log = _LOGGER.warning if self.consecutive_failures == 1 else _LOGGER.debug
        log(
            "Veeam event feed failed (%s); job changes are picked up by the regular poll "
            "until it is back",
            self.last_error,
        )

    def _backoff(self) -> float:
        return min(
            EVENTS_RETRY_MIN_SECONDS * 2 ** (self.consecutive_failures - 1),
            EVENTS_RETRY_MAX_SECONDS,
        )

    async def _handle(self, events: list[Any]) -> None:
        refresh = False
        changed_sessions: set[str] = set()
        for event in events:
            self.events_seen += 1
            event_type = text_field(event, "event_type")
            if event_type in REFRESH_EVENT_TYPES:
                refresh = True
            if event_type == "JobSessionUpdate":
                session_id = text_field(event, "id")
                if session_id:
                    changed_sessions.add(session_id)
                    self._fire_on_status_change(event, session_id)

        if changed_sessions:
            self._coordinator.job_sessions_changed(changed_sessions)
        if refresh:
            await self._coordinator.async_request_refresh()

    def _fire_on_status_change(self, event: Any, session_id: str) -> None:
        status = text_field(event, "status")
        known = session_id in self._statuses
        previous = self._statuses.pop(session_id, None)
        self._statuses[session_id] = status
        while len(self._statuses) > STATUS_MEMORY:
            self._statuses.popitem(last=False)
        if known and previous == status:
            return

        job_id = id_field(event, "job_id")
        self.hass.bus.async_fire(
            EVENT_JOB_SESSION,
            {
                "entry_id": self._entry.entry_id,
                "job_id": job_id,
                "job_name": self._job_name(job_id),
                "job_type": text_field(event, "job_type"),
                "session_id": session_id,
                "status": status,
            },
        )

    def _job_name(self, job_id: str | None) -> str | None:
        data = self._coordinator.data or {}
        for key in ("jobs", "copy_jobs"):
            for item in data.get(key) or []:
                if job_id and str(item.get("id")) == job_id:
                    return item.get("name")
        return None
