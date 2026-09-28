"""Following the event feed (/v8/Events): job changes in seconds, and job sessions by ID."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import httpx
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.veeam_365.const import EVENT_JOB_SESSION

from .conftest import JOB_ID, FakeServer, job_session_json
from .test_integration import JOB, refresh, setup_entry

EVENTS = "events.events_get"
JOB_SESSIONS = "job_session.job_session_get"
JOB_SESSION = "job_session.job_session_get_by_id"
NEW_SESSION = "5e550010-0000-0000-0000-000000000000"


def session_event(session_id: str = NEW_SESSION, status: str = "Running") -> dict:
    return {
        "eventType": "JobSessionUpdate",
        "id": session_id,
        "eTag": 1,
        "jobId": JOB_ID,
        "status": status,
        "jobType": "Backup",
    }


def started_over(server: FakeServer) -> bool:
    """Whether the listener asked for the latest token, and is now following the new one."""
    *_, restart, current = [call["from_"] for call in server.calls_to(EVENTS)]
    return restart == "latest" and current.startswith("token-")


@pytest.fixture(name="feed")
def feed_fixture(server: FakeServer, monkeypatch) -> FakeServer:
    """A server with the feed, and no waiting between retries."""
    server.event_feed = True
    monkeypatch.setattr("custom_components.veeam_365.events.EVENTS_RETRY_MIN_SECONDS", 0)
    return server


async def test_the_feed_starts_from_the_latest_token_and_follows_it(
    hass: HomeAssistant, feed: FakeServer
) -> None:
    await setup_entry(hass)
    await feed.until_listening()
    await feed.deliver_events([])

    tokens = [call["from_"] for call in feed.calls_to(EVENTS)]
    assert tokens[:3] == ["latest", "token-1", "token-2"]
    call = feed.calls_to(EVENTS)[0]
    assert call["limit"] == 10000
    assert call["timeout_seconds"] == 25


async def test_a_job_session_event_refreshes_at_once(hass: HomeAssistant, feed: FakeServer) -> None:
    await setup_entry(hass)
    polls = len(feed.calls_to("job.job_get"))
    feed.job_sessions.append(
        job_session_json(NEW_SESSION, JOB_ID, 0, status="Running", duration_minutes=None)
    )

    await feed.deliver_events([session_event()])
    await hass.async_block_till_done()

    assert len(feed.calls_to("job.job_get")) == polls + 1
    last = hass.states.get(f"sensor.{JOB}_last_session")
    assert last.attributes["session_id"] == NEW_SESSION
    assert last.attributes["status"] == "Running"


async def test_once_the_feed_covers_it_sessions_are_read_by_id(
    hass: HomeAssistant, feed: FakeServer
) -> None:
    entry = await setup_entry(hass)
    await feed.until_listening()
    # The setup poll ran before the feed started, so one more listing is needed first
    await refresh(hass, entry)
    listings = len(feed.calls_to(JOB_SESSIONS))
    assert entry.runtime_data["coordinator"].event_feed_covers_job_sessions

    feed.job_sessions.append(job_session_json(NEW_SESSION, JOB_ID, 0, status="Warning"))
    await feed.deliver_events([session_event(status="Warning")])
    await refresh(hass, entry)

    assert len(feed.calls_to(JOB_SESSIONS)) == listings, "no listing while the feed covers it"
    assert {"job_sessions_id": NEW_SESSION} in feed.calls_to(JOB_SESSION)
    last = hass.states.get(f"sensor.{JOB}_last_session")
    assert last.attributes["session_id"] == NEW_SESSION
    assert last.attributes["raw_value"] == "Warning"


async def test_the_bus_event_fires_when_a_status_changes(
    hass: HomeAssistant, feed: FakeServer
) -> None:
    entry = await setup_entry(hass)
    fired = async_capture_events(hass, EVENT_JOB_SESSION)

    await feed.deliver_events([session_event(status="Running")])
    await feed.deliver_events([session_event(status="Running")])
    await feed.deliver_events([session_event(status="Failed")])
    await hass.async_block_till_done()

    assert [event.data["status"] for event in fired] == ["Running", "Failed"]
    assert fired[-1].data == {
        "entry_id": entry.entry_id,
        "job_id": JOB_ID,
        "job_name": "Daily Mail",
        "job_type": "Backup",
        "session_id": NEW_SESSION,
        "status": "Failed",
    }


async def test_other_events_do_not_refresh(hass: HomeAssistant, feed: FakeServer) -> None:
    await setup_entry(hass)
    polls = len(feed.calls_to("job.job_get"))

    await feed.deliver_events(
        [
            {"eventType": "RestoreSessionUpdate", "id": "r-1", "eTag": 1},
            {"eventType": "ProtectedUserUpdate", "id": "u-1", "eTag": 1},
            {"eventType": "SomethingNewer", "id": "x-1"},
        ]
    )
    await hass.async_block_till_done()

    assert len(feed.calls_to("job.job_get")) == polls


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), TimeoutError()],
    ids=lambda err: type(err).__name__,
)
async def test_after_a_failure_it_starts_over_and_listing_resumes(
    hass: HomeAssistant, feed: FakeServer, failure: Exception
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data["coordinator"]
    await feed.until_listening()
    await refresh(hass, entry)
    assert coordinator.event_feed_covers_job_sessions

    await feed.deliver_events(failure)

    # Back from the latest token: what happened meanwhile is not in the feed, so the next
    # poll lists job sessions again before trusting it
    assert started_over(feed)
    assert not coordinator.event_feed_covers_job_sessions
    listings = len(feed.calls_to(JOB_SESSIONS))
    await refresh(hass, entry)
    assert len(feed.calls_to(JOB_SESSIONS)) == listings + 2
    assert coordinator.event_feed_covers_job_sessions


async def test_an_error_answer_also_starts_over(hass: HomeAssistant, feed: FakeServer) -> None:
    await setup_entry(hass)
    listener = hass.config_entries.async_entries("veeam_365")[0].runtime_data["event_feed"]

    await feed.deliver_events(feed.error("The change token is no longer valid"))

    assert started_over(feed)
    assert listener.consecutive_failures == 0, "recovered on the next request"
    assert listener.connected


async def test_a_server_without_the_feed_keeps_polling(
    hass: HomeAssistant, feed: FakeServer
) -> None:
    feed.latest_errors = [feed.error("Not found")] * 3
    entry = await setup_entry(hass)
    listener = entry.runtime_data["event_feed"]
    await feed.until_listening()

    assert listener.consecutive_failures == 0
    assert len([c for c in feed.calls_to(EVENTS) if c["from_"] == "latest"]) == 4
    assert hass.states.get(f"sensor.{JOB}_last_status").state == "Success"


async def test_the_listener_stops_on_unload(hass: HomeAssistant, feed: FakeServer) -> None:
    entry = await setup_entry(hass)
    await feed.until_listening()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not feed.event_waiting
    assert feed.clients[-1].closed


@pytest.mark.parametrize("version", ["6", "7"])
async def test_older_versions_have_no_feed(
    hass: HomeAssistant, feed: FakeServer, version: str
) -> None:
    feed.license = {"status": "Valid", "type": "Subscription"}
    entry = await setup_entry(hass, api_version=version)

    assert entry.runtime_data["event_feed"] is None
    assert feed.calls_to(EVENTS) == []


async def test_sessions_the_feed_reported_survive_a_failed_poll(
    hass: HomeAssistant, feed: FakeServer
) -> None:
    entry = await setup_entry(hass)
    await feed.until_listening()
    await refresh(hass, entry)
    feed.job_sessions.append(job_session_json(NEW_SESSION, JOB_ID, 0, status="Success"))
    feed.overrides[JOB_SESSION] = httpx.ConnectError("refused")
    await feed.deliver_events([session_event(status="Success")])
    await hass.async_block_till_done()

    del feed.overrides[JOB_SESSION]
    await refresh(hass, entry)

    last = hass.states.get(f"sensor.{JOB}_last_session")
    assert last.attributes["session_id"] == NEW_SESSION
