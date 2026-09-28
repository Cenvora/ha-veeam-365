"""Behavioural tests: setup, polling, failure handling, pruning and buttons.

Run against the fake server in conftest.py, which answers through the real veeam-365 models.
"""

from __future__ import annotations

from datetime import timedelta
import ssl

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from veeam_365.exceptions import VeeamAuthenticationError, VeeamSessionError

from custom_components.veeam_365.const import DOMAIN, REQUEST_TIMEOUT

from .conftest import (
    COPY_JOB_ID,
    ENTRY_DATA,
    JOB_ID,
    OBJECT_REPO_ID,
    ORG_ID,
    OTHER_ORG_ID,
    PROXY_ID,
    REPO_ID,
    FakeServer,
    health_json,
    job_json,
    job_session_json,
    maintenance_json,
    organization_json,
    protected_json,
    proxy_json,
    sync_state_json,
)

JOB = "vb365_job_daily_mail"
COPY_JOB = "vb365_copy_job_mail_copy"
LOCAL_REPO = "vb365_default_backup_repository"
OBJECT_REPO = "vb365_repository_s3_archive"
SERVER = "vb365_server_veeam_example_com"
LICENSE = "vb365_license_veeam_example_com"


async def setup_entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **data},
        unique_id=f"{ENTRY_DATA['host']}:{ENTRY_DATA['port']}",
        title="Veeam 365 (veeam.example.com)",
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data["coordinator"].async_refresh()
    await hass.async_block_till_done()


def state(hass: HomeAssistant, entity_id: str) -> str | None:
    current = hass.states.get(entity_id)
    return current.state if current else None


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://veeam.example.com:4443/v8/Token")
    return httpx.HTTPStatusError(
        f"{status}", request=request, response=httpx.Response(status, request=request)
    )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def test_setup_creates_prefixed_entities(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, f"sensor.{JOB}_last_status") == "Success"
    assert state(hass, f"sensor.{JOB}_backup_type") == "Entire organization"
    assert state(hass, f"sensor.{COPY_JOB}_last_status") == "Warning"
    assert state(hass, f"sensor.{LICENSE}_status") == "Valid"
    assert state(hass, f"sensor.{LICENSE}_used_licenses") == "42"
    assert state(hass, f"sensor.{SERVER}_product_version") == "8.1.0.305"
    assert state(hass, f"binary_sensor.{SERVER}_connected") == STATE_ON
    assert state(hass, f"binary_sensor.{SERVER}_health_ok") == STATE_ON
    assert state(hass, f"button.{JOB}_start") is not None
    assert state(hass, f"button.{COPY_JOB}_start") is not None
    assert state(hass, f"button.{LOCAL_REPO}_synchronize_cache") is not None

    # Unique IDs are unchanged by the renaming, and IDs are strings, not uuid.UUID
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_job_{JOB_ID}_last_status")
        == f"sensor.{JOB}_last_status"
    )
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"job_{JOB_ID}"), entry.entry_id
    )
    assert device is not None and device.name == "VB365 Job Daily Mail"
    assert device.model == "Backup Job"

    # The client is built with a timeout and Home Assistant's shared SSL context
    client = server.clients[-1]
    assert client.kwargs["timeout"] == REQUEST_TIMEOUT
    assert isinstance(client.kwargs["verify_ssl"], ssl.SSLContext)
    assert client.kwargs["api_version"] == "v8"


async def test_repositories(hass: HomeAssistant, server: FakeServer) -> None:
    await setup_entry(hass)

    # Local: capacity minus free space, in GiB
    used = hass.states.get(f"sensor.{LOCAL_REPO}_used_space")
    assert float(used.state) == 60.0
    assert used.attributes["unit_of_measurement"] == "GiB"
    assert state(hass, f"sensor.{LOCAL_REPO}_type") == "Local"
    # Object storage: its own used space, not the cache's
    assert float(state(hass, f"sensor.{OBJECT_REPO}_used_space")) == 5.0
    assert state(hass, f"sensor.{OBJECT_REPO}_type") == "Amazon S3"
    assert state(hass, f"sensor.{OBJECT_REPO}_immutability_days") == "30"

    assert state(hass, f"binary_sensor.{LOCAL_REPO}_accessible") == STATE_ON
    assert state(hass, f"binary_sensor.{LOCAL_REPO}_cache_in_sync") == STATE_ON
    assert state(hass, f"binary_sensor.{OBJECT_REPO}_cache_in_sync") == STATE_OFF
    assert state(hass, f"binary_sensor.{OBJECT_REPO}_immutable") == STATE_ON


@pytest.mark.parametrize("version", ["6", "7"])
async def test_list_responses_of_older_versions(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    """v6 and v7 return plain lists, which used to read as empty."""
    server.license = {"status": "Valid", "type": "Subscription", "totalNumber": 10}
    await setup_entry(hass, api_version=version)

    assert state(hass, f"sensor.{JOB}_last_status") == "Success"
    assert state(hass, f"sensor.{COPY_JOB}_last_status") == "Warning"
    assert state(hass, f"sensor.{LOCAL_REPO}_type") == "Local"
    # Only v8 reports whether a repository is in the Invalid state
    assert state(hass, f"binary_sensor.{LOCAL_REPO}_accessible") == STATE_UNKNOWN
    # Not paged, so no limit/offset
    assert server.calls_to("job.job_get") == [{}]
    assert state(hass, f"sensor.{LICENSE}_total_licenses") == "10"
    if version == "6":
        # Fields v6 does not have read as unknown, never as a leaked sentinel
        assert state(hass, f"sensor.{JOB}_last_backup") == STATE_UNKNOWN
        assert state(hass, f"sensor.{LICENSE}_grace_period_expiration") == STATE_UNKNOWN


async def test_v8_collections_are_paged(hass: HomeAssistant, server: FakeServer) -> None:
    """The server defaults to 30 per page, which truncated larger installations. Asking for
    its maximum of 10,000 lists anything short of that in one request."""
    server.collections["job.job_get"] = [
        job_json(job_id=f"00000000-0000-0000-0000-{index:012d}", name=f"Job {index}")
        for index in range(250)
    ]
    entry = await setup_entry(hass)

    assert server.calls_to("job.job_get") == [{"limit": 10000, "offset": 0}]
    assert len(entry.runtime_data["coordinator"].data["jobs"]) == 250


async def test_paging_follows_a_server_capped_page_size(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.max_page_size = 30
    server.collections["job.job_get"] = [
        job_json(job_id=f"00000000-0000-0000-0000-{index:012d}", name=f"Job {index}")
        for index in range(65)
    ]
    entry = await setup_entry(hass)

    assert [call["offset"] for call in server.calls_to("job.job_get")] == [0, 30, 60]
    assert len(entry.runtime_data["coordinator"].data["jobs"]) == 65


async def test_refused_credentials_start_reauth(hass: HomeAssistant, server: FakeServer) -> None:
    server.connect_error = VeeamAuthenticationError("Veeam login failed")
    entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)
    assert server.clients[-1].closed


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        TimeoutError(),
        http_error(503),
        VeeamSessionError("undecodable"),
        OSError("no route to host"),
    ],
    ids=lambda err: type(err).__name__,
)
async def test_unreachable_server_is_retried(
    hass: HomeAssistant, server: FakeServer, error: Exception
) -> None:
    """Setup used to return False, which never retries."""
    server.connect_error = error
    entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress()
    )
    assert server.clients[-1].closed


async def test_unload_closes_the_client(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    client = entry.runtime_data["veeam_client"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert client.closed


# ---------------------------------------------------------------------------
# Polling failures
# ---------------------------------------------------------------------------


async def test_unreachable_during_polling(hass: HomeAssistant, server: FakeServer) -> None:
    """The live failure: everything unknown, yet Connected and Health OK stayed on."""
    entry = await setup_entry(hass)
    server.overrides["job.job_get"] = httpx.ConnectError("connection refused")

    await refresh(hass, entry)

    assert not entry.runtime_data["coordinator"].last_update_success
    assert state(hass, f"sensor.{JOB}_last_status") == STATE_UNAVAILABLE
    # Connectivity sensors report the failure rather than going unavailable themselves
    assert state(hass, f"binary_sensor.{SERVER}_connected") == STATE_OFF
    assert state(hass, f"binary_sensor.{SERVER}_health_ok") == STATE_OFF
    assert state(hass, f"sensor.{SERVER}_last_successful_poll") not in (None, STATE_UNAVAILABLE)


async def test_a_rejected_session_is_retried_once(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    attempts = []

    def flaky(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            return VeeamSessionError("Expecting value: line 1 column 1 (char 0)")
        del server.overrides["job.job_get"]
        return server.answer("job.job_get", kwargs)

    server.overrides["job.job_get"] = flaky
    await refresh(hass, entry)

    assert entry.runtime_data["coordinator"].last_update_success
    assert len(attempts) == 2
    assert state(hass, f"sensor.{JOB}_last_status") == "Success"


async def test_a_session_rejected_twice_fails_the_update(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides["job.job_get"] = VeeamSessionError("Expecting value")

    await refresh(hass, entry)

    coordinator = entry.runtime_data["coordinator"]
    assert not coordinator.last_update_success
    assert len(server.calls_to("job.job_get")) == 1 + 2
    assert state(hass, f"binary_sensor.{SERVER}_connected") == STATE_OFF


async def test_refused_credentials_while_polling_start_reauth(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides["job.job_get"] = VeeamAuthenticationError("refused")

    await refresh(hass, entry)

    assert any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_one_failing_endpoint_only_affects_its_entities(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides["license_.license_get"] = server.error("Insufficient permissions")

    await refresh(hass, entry)

    data = entry.runtime_data["coordinator"].data
    assert data["fetch_ok"]["license_info"] is False
    assert state(hass, f"sensor.{LICENSE}_status") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{JOB}_last_status") == "Success"
    assert state(hass, f"binary_sensor.{SERVER}_connected") == STATE_ON
    health = hass.states.get(f"binary_sensor.{SERVER}_health_ok")
    assert health.state == STATE_OFF
    assert health.attributes["failed_endpoints"] == ["license_info"]


async def test_a_failing_jobs_endpoint_fails_the_update(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides["job.job_get"] = server.error("Internal error")

    await refresh(hass, entry)

    assert not entry.runtime_data["coordinator"].last_update_success


async def test_a_failed_first_poll_is_retried(hass: HomeAssistant, server: FakeServer) -> None:
    server.overrides["job.job_get"] = server.error("Internal error")
    entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert server.clients[-1].closed


async def test_unset_values_do_not_leak_into_states(
    hass: HomeAssistant, server: FakeServer
) -> None:
    job = {"id": JOB_ID, "name": "Daily Mail"}
    server.collections["job.job_get"] = [job]
    server.license = {"status": "Valid"}
    await setup_entry(hass)

    assert state(hass, f"sensor.{JOB}_last_backup") == STATE_UNKNOWN
    assert state(hass, f"sensor.{JOB}_backup_type") == "Unknown"
    assert state(hass, f"sensor.{JOB}_enabled") == STATE_UNKNOWN
    assert state(hass, f"sensor.{LICENSE}_licensed_to") == STATE_UNKNOWN
    assert state(hass, f"sensor.{LICENSE}_total_licenses") == STATE_UNKNOWN
    for current in hass.states.async_all():
        assert "Unset" not in current.state


async def test_null_and_unknown_enum_values_keep_the_endpoint(
    hass: HomeAssistant, server: FakeServer
) -> None:
    """One odd enum used to wipe the whole endpoint."""
    server.collections["job.job_get"] = [
        job_json(lastStatus="SomethingNew"),
        job_json(job_id=COPY_JOB_ID, name="Second", lastStatus=None, backupType=None),
    ]
    server.license = {"status": "Valid", "type": None}
    await setup_entry(hass)

    current = hass.states.get(f"sensor.{JOB}_last_status")
    assert current.state == "Something New"
    assert current.attributes["raw_value"] == "SomethingNew"
    assert state(hass, "sensor.vb365_job_second_last_status") == "Unknown"
    assert state(hass, f"sensor.{LICENSE}_type") == "Unknown"


async def test_license_repair_follows_each_poll(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    issues = ir.async_get(hass)
    issue_id = f"unsupported_license_{entry.entry_id}"
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    server.license = {"status": "Valid", "type": "Community"}
    await refresh(hass, entry)
    assert issues.async_get_issue(DOMAIN, issue_id) is not None

    server.license = {"status": "Valid", "type": "Subscription"}
    await refresh(hass, entry)
    assert issues.async_get_issue(DOMAIN, issue_id) is None


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


def _device(hass: HomeAssistant, identifier: str, entry: MockConfigEntry | None = None):
    registry = dr.async_get(hass)
    if entry is None:
        (entry,) = hass.config_entries.async_entries(DOMAIN)[:1]
    return registry.async_get_device_by_identifier((DOMAIN, identifier), entry.entry_id)


async def test_a_deleted_repository_is_pruned_everywhere(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    repositories = "backup_repository.backup_repository_get_repositories"
    object_repo = server.collections[repositories].pop()

    await refresh(hass, entry)

    assert _device(hass, f"repository_{OBJECT_REPO_ID}") is None
    registry = er.async_get(hass)
    leftovers = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if OBJECT_REPO_ID in (entity.unique_id or "")
    ]
    assert leftovers == [], "sensors, binary sensors and the button should all go"
    assert _device(hass, f"repository_{REPO_ID}") is not None

    # And it comes back if the server reports it again
    server.collections[repositories].append(object_repo)
    await refresh(hass, entry)
    assert state(hass, f"sensor.{OBJECT_REPO}_used_space") is not None
    assert _device(hass, f"repository_{OBJECT_REPO_ID}") is not None


async def test_a_failed_fetch_prunes_nothing(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    server.overrides["backup_repository.backup_repository_get_repositories"] = server.error()

    await refresh(hass, entry)

    assert _device(hass, f"repository_{REPO_ID}") is not None
    assert _device(hass, f"repository_{OBJECT_REPO_ID}") is not None
    assert state(hass, f"sensor.{LOCAL_REPO}_used_space") == STATE_UNAVAILABLE
    assert state(hass, f"button.{LOCAL_REPO}_synchronize_cache") == STATE_UNAVAILABLE


async def test_an_empty_collection_prunes_nothing(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    server.collections["copy_job.copy_job_get"] = []

    await refresh(hass, entry)

    assert _device(hass, f"copy_job_{COPY_JOB_ID}") is not None


async def test_pruning_leaves_other_entries_devices_alone(
    hass: HomeAssistant, server: FakeServer
) -> None:
    """The sweep used to remove matching devices whichever entry they belonged to."""
    other = MockConfigEntry(domain=DOMAIN, unique_id="other:4443")
    other.add_to_hass(hass)
    device_reg = dr.async_get(hass)
    device_reg.async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={(DOMAIN, f"copy_job_{COPY_JOB_ID}")},
        name="Other server's copy job",
    )
    entry = await setup_entry(hass)

    server.collections["copy_job.copy_job_get"] = [
        {"id": "99999999-9999-9999-9999-999999999999", "name": "Another"}
    ]
    await refresh(hass, entry)

    assert _device(hass, f"copy_job_{COPY_JOB_ID}", entry) is None
    assert _device(hass, f"copy_job_{COPY_JOB_ID}", other) is not None


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)


async def test_job_buttons_call_the_real_operations(
    hass: HomeAssistant, server: FakeServer
) -> None:
    """The buttons imported model modules that do not exist, and silently did nothing."""
    await setup_entry(hass)

    await press(hass, f"button.{JOB}_start")
    (start,) = server.calls_to("job.job_start_action")
    assert start["job_id"] == JOB_ID
    assert start["body"].full is False

    for verb in ("stop", "enable", "disable"):
        await press(hass, f"button.{JOB}_{verb}")
        assert server.calls_to(f"job.job_{verb}_action") == [{"job_id": JOB_ID}]

    await press(hass, f"button.{COPY_JOB}_start")
    # v8's copy job start takes no options
    assert server.calls_to("copy_job.copy_job_start") == [{"id": COPY_JOB_ID}]

    await press(hass, f"button.{LOCAL_REPO}_synchronize_cache")
    assert server.calls_to("backup_repository.backup_repository_start_synchronize_action") == [
        {"repository_id": REPO_ID}
    ]


async def test_v6_copy_job_start_sends_options(hass: HomeAssistant, server: FakeServer) -> None:
    server.license = {"status": "Valid", "type": "Subscription"}
    await setup_entry(hass, api_version="6")

    await press(hass, f"button.{COPY_JOB}_start")

    (call,) = server.calls_to("copy_job.copy_job_start")
    assert call["id"] == COPY_JOB_ID
    assert call["body"].full is False


async def test_a_refused_action_is_reported(hass: HomeAssistant, server: FakeServer) -> None:
    await setup_entry(hass)
    server.overrides["job.job_start_action"] = server.error("Job is already running")

    with pytest.raises(HomeAssistantError) as raised:
        await press(hass, f"button.{JOB}_start")

    assert raised.value.translation_key == "job_start_failed"
    assert raised.value.translation_placeholders["error"] == "Job is already running"
    assert raised.value.translation_placeholders["job_name"] == "Daily Mail"


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), VeeamSessionError("undecodable"), TimeoutError()],
    ids=lambda err: type(err).__name__,
)
async def test_an_action_that_fails_to_send_is_reported(
    hass: HomeAssistant, server: FakeServer, error: Exception
) -> None:
    await setup_entry(hass)
    server.overrides["backup_repository.backup_repository_start_synchronize_action"] = error

    with pytest.raises(HomeAssistantError) as raised:
        await press(hass, f"button.{LOCAL_REPO}_synchronize_cache")

    assert raised.value.translation_key == "repository_rescan_failed"


async def test_refused_credentials_on_press_start_reauth(
    hass: HomeAssistant, server: FakeServer
) -> None:
    await setup_entry(hass)
    server.overrides["job.job_stop_action"] = VeeamAuthenticationError("refused")

    with pytest.raises(HomeAssistantError):
        await press(hass, f"button.{JOB}_stop")
    await hass.async_block_till_done()

    assert any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress()
    )


# ---------------------------------------------------------------------------
# The server's health report (API v8)
# ---------------------------------------------------------------------------

SERVICE_HEALTH = f"binary_sensor.{SERVER}_service_health"


async def test_a_healthy_server_reports_ok(hass: HomeAssistant, server: FakeServer) -> None:
    await setup_entry(hass)

    health = hass.states.get(SERVICE_HEALTH)
    assert health.state == STATE_OFF
    assert health.attributes["raw_value"] == "Healthy"
    assert health.attributes["problems"] == []
    assert health.attributes["checks"]["nats"]["status"] == "Healthy"
    assert health.attributes["checks"]["database"]["description"].startswith("Connection to")


async def test_an_unhealthy_check_is_a_problem(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    server.health = health_json("Unhealthy", nats="Healthy")
    server.health["entries"]["database"]["description"] = "Cannot reach PostgreSQL."

    await refresh(hass, entry)

    health = hass.states.get(SERVICE_HEALTH)
    assert health.state == STATE_ON
    assert health.attributes["raw_value"] == "Unhealthy"
    assert health.attributes["problems"] == ["Cannot reach PostgreSQL."]
    # The endpoint answered, so this is the server's verdict, not a failed poll
    assert state(hass, f"binary_sensor.{SERVER}_health_ok") == STATE_ON


async def test_an_unhealthy_report_sent_as_an_error_is_still_read(
    hass: HomeAssistant, server: FakeServer
) -> None:
    """A 503 carrying the report parses as the error model; it must not read as a failure."""
    entry = await setup_entry(hass)
    server.overrides["health.health_get"] = server.models.RESTExceptionInfo.from_dict(
        health_json("Unhealthy", nats="Unhealthy")
    )

    await refresh(hass, entry)

    health = hass.states.get(SERVICE_HEALTH)
    assert health.state == STATE_ON
    assert len(health.attributes["problems"]) == 2


async def test_a_failing_health_endpoint_goes_unavailable(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides["health.health_get"] = server.error("Internal error")

    await refresh(hass, entry)

    assert state(hass, SERVICE_HEALTH) == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{JOB}_last_status") == "Success"
    health_ok = hass.states.get(f"binary_sensor.{SERVER}_health_ok")
    assert health_ok.state == STATE_OFF
    assert health_ok.attributes["failed_endpoints"] == ["health"]


async def test_a_report_without_a_status_is_unknown(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.health = {"entries": {}}
    await setup_entry(hass)

    assert state(hass, SERVICE_HEALTH) == STATE_UNKNOWN


@pytest.mark.parametrize("version", ["6", "7"])
async def test_older_versions_have_no_health_report(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    """The endpoint is v8 only: older servers get no entity and no failing endpoint."""
    entry = await setup_entry(hass, api_version=version)

    assert hass.states.get(SERVICE_HEALTH) is None
    assert server.calls_to("health.health_get") == []
    assert "health" not in entry.runtime_data["coordinator"].data["fetch_ok"]
    assert state(hass, f"binary_sensor.{SERVER}_health_ok") == STATE_ON


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------

PROXY = "vb365_proxy_proxy01"
PROXIES = "proxy.proxy_get_proxies"


async def test_proxies_get_a_device_each(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)

    online = hass.states.get(f"binary_sensor.{PROXY}_online")
    assert online.state == STATE_ON
    assert online.attributes["raw_value"] == "Online"
    assert online.attributes["fqdn"] == "proxy01.example.com"
    assert online.attributes["roles"] == ["Processor"]
    maintenance = hass.states.get(f"sensor.{PROXY}_maintenance_mode")
    assert maintenance.state == "Disabled"
    assert maintenance.attributes["raw_value"] == "Disabled"
    assert state(hass, f"sensor.{PROXY}_cpu_usage") == "23.5"
    assert state(hass, f"sensor.{PROXY}_memory_usage") == "61.0"
    assert state(hass, f"sensor.{PROXY}_version") == "8.1.0.305"
    assert state(hass, f"sensor.{PROXY}_operating_system") == "Windows"

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"proxy_{PROXY_ID}"), entry.entry_id
    )
    assert device is not None and device.name == "VB365 Proxy proxy01"
    assert device.model == "Backup Proxy"
    # v8 pages the proxies like every other collection
    assert server.calls_to(PROXIES) == [{"limit": 10000, "offset": 0}]


async def test_maintenance_and_going_offline_follow_the_server(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.collections[PROXIES] = [
        proxy_json(
            status="Offline",
            maintenanceModeState="Enabling",
            cpuUsagePercent=None,
            memoryUsagePercent=None,
        )
    ]

    await refresh(hass, entry)

    assert state(hass, f"binary_sensor.{PROXY}_online") == STATE_OFF
    assert state(hass, f"sensor.{PROXY}_maintenance_mode") == "Enabling"
    # An offline proxy reports no usage: unknown, not zero and not unavailable
    assert state(hass, f"sensor.{PROXY}_cpu_usage") == STATE_UNKNOWN
    assert state(hass, f"sensor.{PROXY}_memory_usage") == STATE_UNKNOWN


async def test_a_proxy_first_seen_offline_still_gets_its_v8_sensors(
    hass: HomeAssistant, server: FakeServer
) -> None:
    """Which sensors exist depends on the API version, not on what one poll reported."""
    server.collections[PROXIES] = [{"id": PROXY_ID, "hostName": "proxy01", "status": "Offline"}]
    entry = await setup_entry(hass)

    assert state(hass, f"sensor.{PROXY}_cpu_usage") == STATE_UNKNOWN

    server.collections[PROXIES] = [proxy_json()]
    await refresh(hass, entry)
    assert state(hass, f"sensor.{PROXY}_cpu_usage") == "23.5"


@pytest.mark.parametrize("version", ["6", "7"])
async def test_older_versions_only_report_whether_a_proxy_is_online(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    await setup_entry(hass, api_version=version)

    assert state(hass, f"binary_sensor.{PROXY}_online") == STATE_ON
    for suffix in ("maintenance_mode", "cpu_usage", "memory_usage", "version"):
        assert hass.states.get(f"sensor.{PROXY}_{suffix}") is None, suffix
    # Not paged before v8
    assert server.calls_to(PROXIES) == [{}]


async def test_a_failing_proxies_endpoint_only_affects_proxies(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides[PROXIES] = server.error("Insufficient permissions")

    await refresh(hass, entry)

    assert state(hass, f"binary_sensor.{PROXY}_online") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{PROXY}_cpu_usage") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{JOB}_last_status") == "Success"
    assert _device(hass, f"proxy_{PROXY_ID}") is not None, "a failed fetch prunes nothing"
    health_ok = hass.states.get(f"binary_sensor.{SERVER}_health_ok")
    assert health_ok.attributes["failed_endpoints"] == ["proxies"]


async def test_a_removed_proxy_is_pruned(hass: HomeAssistant, server: FakeServer) -> None:
    second = "88888888-8888-8888-8888-888888888888"
    server.collections[PROXIES] = [proxy_json(), proxy_json(second, "proxy02")]
    entry = await setup_entry(hass)
    assert _device(hass, f"proxy_{second}") is not None

    server.collections[PROXIES] = [proxy_json()]
    await refresh(hass, entry)

    assert _device(hass, f"proxy_{second}") is None
    assert hass.states.get("binary_sensor.vb365_proxy_proxy02_online") is None
    assert _device(hass, f"proxy_{PROXY_ID}") is not None


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

ORG = "vb365_organization_contoso_onmicrosoft_com"
ORGANIZATIONS = "organization.organization_get"
SYNC_START = "organization_sync.organization_sync_start"


async def test_organizations_get_a_device_each(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)

    last_backup = hass.states.get(f"sensor.{ORG}_last_backup")
    assert last_backup.state == "2026-09-27T05:10:00+00:00"
    assert last_backup.attributes["services"] == ["Exchange", "SharePoint"]
    assert last_backup.attributes["office_name"] == "Contoso"
    assert state(hass, f"sensor.{ORG}_licensed_users") == "250"
    assert state(hass, f"sensor.{ORG}_new_users") == "3"
    assert state(hass, f"sensor.{ORG}_type") == "Office365"
    assert state(hass, f"sensor.{ORG}_region") == "Worldwide"
    assert state(hass, f"binary_sensor.{ORG}_backed_up") == STATE_ON

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"organization_{ORG_ID}"), entry.entry_id
    )
    assert device is not None and device.name == "VB365 Organization contoso.onmicrosoft.com"
    assert device.model == "Microsoft 365 Organization"


async def test_organization_sync_state_v8(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)

    sync = hass.states.get(f"binary_sensor.{ORG}_sync")
    assert sync.state == STATE_OFF
    assert sync.attributes["raw_value"] == "Success"
    assert sync.attributes["parts"]["users"]["last_result"] == "Success"
    assert state(hass, f"sensor.{ORG}_last_sync") == "2026-09-28T02:15:00+00:00"
    status = hass.states.get(f"sensor.{ORG}_sync_status")
    assert status.state == "Queued"
    assert status.attributes["next_sync"].isoformat() == "2026-09-28T10:30:00+00:00"
    # Every organization's state in one call
    assert len(server.calls_to("organization_sync.organization_sync_get_states")) == 1

    server.sync_states[ORG_ID] = sync_state_json(result="Error", currentSyncState=None)
    await refresh(hass, entry)

    sync = hass.states.get(f"binary_sensor.{ORG}_sync")
    assert sync.state == STATE_ON
    assert sync.attributes["error"] == "Access to the Microsoft Graph API was denied."
    # Nothing queued or running
    assert state(hass, f"sensor.{ORG}_sync_status") == "Idle"


async def test_an_organization_never_synced_reads_unknown(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.sync_states = {}
    await setup_entry(hass)

    assert state(hass, f"binary_sensor.{ORG}_sync") == STATE_UNKNOWN
    assert state(hass, f"sensor.{ORG}_last_sync") == STATE_UNKNOWN
    assert state(hass, f"sensor.{ORG}_licensed_users") == "250"


async def test_a_failing_sync_endpoint_only_affects_sync_entities(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides["organization_sync.organization_sync_get_states"] = server.error()

    await refresh(hass, entry)

    assert state(hass, f"binary_sensor.{ORG}_sync") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{ORG}_sync_status") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{ORG}_licensed_users") == "250"
    health_ok = hass.states.get(f"binary_sensor.{SERVER}_health_ok")
    assert health_ok.attributes["failed_endpoints"] == ["organization_sync"]


async def test_unreadable_licensing_costs_only_the_counters(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.licensing = {}
    await setup_entry(hass)

    assert state(hass, f"sensor.{ORG}_licensed_users") == STATE_UNKNOWN
    assert state(hass, f"sensor.{ORG}_last_backup") == "2026-09-27T05:10:00+00:00"


async def test_v7_asks_each_organization_for_its_sync_state(
    hass: HomeAssistant, server: FakeServer
) -> None:
    await setup_entry(hass, api_version="7")

    assert server.calls_to("organization_sync.organization_sync_get_state") == [
        {"organization_id": ORG_ID}
    ]
    assert state(hass, f"binary_sensor.{ORG}_sync") == STATE_OFF
    assert state(hass, f"sensor.{ORG}_last_sync") == "2026-09-28T02:15:00+00:00"
    # v7 reports no sync in progress or queued
    assert hass.states.get(f"sensor.{ORG}_sync_status") is None
    assert state(hass, f"button.{ORG}_synchronize") is not None


async def test_v6_has_no_organization_sync(hass: HomeAssistant, server: FakeServer) -> None:
    server.license = {"status": "Valid", "type": "Subscription"}
    await setup_entry(hass, api_version="6")

    assert state(hass, f"sensor.{ORG}_licensed_users") == "250"
    assert hass.states.get(f"binary_sensor.{ORG}_sync") is None
    assert hass.states.get(f"sensor.{ORG}_last_sync") is None
    assert hass.states.get(f"button.{ORG}_synchronize") is None
    health_ok = hass.states.get(f"binary_sensor.{SERVER}_health_ok")
    assert health_ok.state == STATE_ON


@pytest.mark.parametrize("version", ["7", "8"])
async def test_synchronize_starts_an_incremental_sync(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    await setup_entry(hass, api_version=version)

    await press(hass, f"button.{ORG}_synchronize")

    (call,) = server.calls_to(SYNC_START)
    assert call["organization_id"] == ORG_ID
    assert call["body"].to_dict() == {"type": "Incremental"}


async def test_a_refused_synchronize_is_reported(hass: HomeAssistant, server: FakeServer) -> None:
    await setup_entry(hass)
    server.overrides[SYNC_START] = server.error("Synchronization is already running")

    with pytest.raises(HomeAssistantError) as raised:
        await press(hass, f"button.{ORG}_synchronize")

    assert raised.value.translation_key == "organization_synchronize_failed"
    assert raised.value.translation_placeholders["organization_name"] == "contoso.onmicrosoft.com"


async def test_a_removed_organization_is_pruned(hass: HomeAssistant, server: FakeServer) -> None:
    second = "12121212-1212-1212-1212-121212121212"
    server.collections[ORGANIZATIONS] = [
        organization_json(),
        organization_json(second, "fabrikam.onmicrosoft.com"),
    ]
    entry = await setup_entry(hass)
    assert _device(hass, f"organization_{second}") is not None

    server.collections[ORGANIZATIONS] = [organization_json()]
    await refresh(hass, entry)

    assert _device(hass, f"organization_{second}") is None
    assert _device(hass, f"organization_{ORG_ID}") is not None


# ---------------------------------------------------------------------------
# Protected object counts (API v8)
# ---------------------------------------------------------------------------

PROTECTED_USERS = "protected_data.protected_data_get_protected_users"
PROTECTED_KINDS = ("users", "groups", "sites", "teams")


async def count(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run the hourly count now, as its first run in the background does after setup."""
    await entry.runtime_data["protected_counts"].async_refresh()
    await hass.async_block_till_done()


async def test_protected_objects_are_counted_per_organization(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    users = hass.states.get(f"sensor.{ORG}_protected_users")
    # The fourth user belongs to another organization
    assert users.state == "3"
    assert users.attributes["counted_at"] is not None
    assert state(hass, f"sensor.{ORG}_protected_groups") == "1"
    assert state(hass, f"sensor.{ORG}_protected_sites") == "2"
    # Counted, and there are none: zero rather than unknown
    assert state(hass, f"sensor.{ORG}_protected_teams") == "0"
    assert server.calls_to(PROTECTED_USERS) == [{"limit": 10000, "offset": 0}]
    assert entry.runtime_data["protected_counts"].update_interval.total_seconds() == 3600


async def test_the_regular_poll_does_not_count(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    calls = len(server.calls_to(PROTECTED_USERS))

    await refresh(hass, entry)
    await refresh(hass, entry)

    assert len(server.calls_to(PROTECTED_USERS)) == calls


async def test_counting_follows_every_page(hass: HomeAssistant, server: FakeServer) -> None:
    server.collections[PROTECTED_USERS] = [protected_json(i) for i in range(5)]
    server.max_page_size = 2
    await setup_entry(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert state(hass, f"sensor.{ORG}_protected_users") == "5"
    assert [call["offset"] for call in server.calls_to(PROTECTED_USERS)] == [0, 2, 4]


async def test_one_kind_failing_keeps_its_last_count(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    await count(hass, entry)
    server.overrides[PROTECTED_USERS] = server.error("Internal error")
    server.collections["protected_data.protected_data_get_protected_sites"] = []

    await count(hass, entry)

    assert state(hass, f"sensor.{ORG}_protected_users") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{ORG}_protected_sites") == "0"
    # The last good count is kept for when it is back
    counts = entry.runtime_data["protected_counts"].data["counts"]
    assert counts[ORG_ID]["users"] == 3

    del server.overrides[PROTECTED_USERS]
    await count(hass, entry)
    assert state(hass, f"sensor.{ORG}_protected_users") == "3"


async def test_counting_failing_entirely_leaves_the_poll_alone(
    hass: HomeAssistant, server: FakeServer
) -> None:
    for kind in PROTECTED_KINDS:
        server.overrides[f"protected_data.protected_data_get_protected_{kind}"] = server.error()
    entry = await setup_entry(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    for kind in PROTECTED_KINDS:
        assert state(hass, f"sensor.{ORG}_protected_{kind}") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{ORG}_licensed_users") == "250"
    assert not entry.runtime_data["protected_counts"].last_update_success
    assert entry.runtime_data["coordinator"].last_update_success


async def test_an_unreachable_server_while_counting_fails_only_the_count(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    await count(hass, entry)
    server.overrides[PROTECTED_USERS] = httpx.ConnectError("refused")

    await count(hass, entry)

    assert state(hass, f"sensor.{ORG}_protected_groups") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{JOB}_last_status") == "Success"


async def test_a_new_organization_is_counted_too(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    await count(hass, entry)
    other = "vb365_organization_fabrikam_onmicrosoft_com"
    server.collections[ORGANIZATIONS].append(
        organization_json(OTHER_ORG_ID, "fabrikam.onmicrosoft.com")
    )

    await refresh(hass, entry)

    # Counted on the last run already; its sensors appear with the organization
    assert state(hass, f"sensor.{other}_protected_users") == "1"


@pytest.mark.parametrize("version", ["6", "7"])
async def test_older_versions_count_nothing(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    server.license = {"status": "Valid", "type": "Subscription"}
    entry = await setup_entry(hass, api_version=version)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.runtime_data["protected_counts"] is None
    assert hass.states.get(f"sensor.{ORG}_protected_users") is None
    assert server.calls_to(PROTECTED_USERS) == []


# ---------------------------------------------------------------------------
# Repository maintenance (API v8, VB365 8.6 and later)
# ---------------------------------------------------------------------------

MAINTENANCE_SESSIONS = "repository_maintenance_session.repository_maintenance_sessions_get"
START_MAINTENANCE = "repository_maintenance_session.repository_maintenance_sessions_start_action"
STOP_MAINTENANCE = "repository_maintenance_session.repository_maintenance_sessions_stop_action"
SESSION_1 = "a1a1a1a1-0000-0000-0000-000000000001"
SESSION_2 = "a1a1a1a1-0000-0000-0000-000000000002"


async def setup_maintenance_server(hass: HomeAssistant, server: FakeServer) -> MockConfigEntry:
    """Set up against a VB365 8.6 server, the first to serve maintenance sessions."""
    server.version = "8.6.0.1004"
    return await setup_entry(hass)


async def test_a_repository_never_in_maintenance(hass: HomeAssistant, server: FakeServer) -> None:
    await setup_maintenance_server(hass, server)

    assert state(hass, f"binary_sensor.{LOCAL_REPO}_maintenance") == STATE_OFF
    status = hass.states.get(f"sensor.{LOCAL_REPO}_maintenance_status")
    assert status.state == "Never"
    assert status.attributes["raw_value"] is None


async def test_an_active_session_puts_its_repositories_in_maintenance(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_maintenance_server(hass, server)
    server.collections[MAINTENANCE_SESSIONS] = [
        # An older, failed session on both repositories...
        maintenance_json(
            SESSION_1,
            "Failed",
            "2026-09-01T10:00:00+00:00",
            [REPO_ID, OBJECT_REPO_ID],
            endTime="2026-09-01T10:05:00+00:00",
            errorMessage="Repository is locked by another process.",
        ),
        # ...and a running one on the local repository only
        maintenance_json(SESSION_2, "Running", "2026-09-28T09:00:00+00:00", [REPO_ID]),
    ]

    await refresh(hass, entry)

    maintenance = hass.states.get(f"binary_sensor.{LOCAL_REPO}_maintenance")
    assert maintenance.state == STATE_ON
    assert maintenance.attributes["session_id"] == SESSION_2
    assert state(hass, f"sensor.{LOCAL_REPO}_maintenance_status") == "Running"
    # The object repository's latest session is the failed one
    assert state(hass, f"binary_sensor.{OBJECT_REPO}_maintenance") == STATE_OFF
    status = hass.states.get(f"sensor.{OBJECT_REPO}_maintenance_status")
    assert status.state == "Failed"
    assert status.attributes["error"] == "Repository is locked by another process."


async def test_an_active_session_wins_over_a_later_finished_one(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.collections[MAINTENANCE_SESSIONS] = [
        maintenance_json(SESSION_1, "Preparing", "2026-09-28T08:00:00+00:00", [REPO_ID]),
        maintenance_json(SESSION_2, "Finished", "2026-09-28T09:00:00+00:00", [REPO_ID]),
    ]
    await setup_maintenance_server(hass, server)

    assert state(hass, f"binary_sensor.{LOCAL_REPO}_maintenance") == STATE_ON
    assert state(hass, f"sensor.{LOCAL_REPO}_maintenance_status") == "Preparing"


async def test_start_maintenance_waits_rather_than_force_stopping(
    hass: HomeAssistant, server: FakeServer
) -> None:
    await setup_maintenance_server(hass, server)

    await press(hass, f"button.{LOCAL_REPO}_start_maintenance")

    (call,) = server.calls_to(START_MAINTENANCE)
    assert call["body"].to_dict() == {
        "repositoryIds": [REPO_ID],
        "waitingConfig": {
            "waitForSessionsTimeout": 60,
            "forceStopSessions": False,
            "forceStopSessionsTimeout": 10,
        },
    }


async def test_stop_maintenance_stops_the_active_session(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.collections[MAINTENANCE_SESSIONS] = [
        maintenance_json(SESSION_2, "Running", "2026-09-28T09:00:00+00:00", [REPO_ID])
    ]
    await setup_maintenance_server(hass, server)

    await press(hass, f"button.{LOCAL_REPO}_stop_maintenance")

    assert server.calls_to(STOP_MAINTENANCE) == [{"session_id": SESSION_2}]


async def test_stop_maintenance_without_a_session_says_so(
    hass: HomeAssistant, server: FakeServer
) -> None:
    await setup_maintenance_server(hass, server)

    with pytest.raises(HomeAssistantError) as raised:
        await press(hass, f"button.{LOCAL_REPO}_stop_maintenance")

    assert raised.value.translation_key == "repository_stop_maintenance_failed"
    assert raised.value.translation_placeholders["error"] == (
        "the repository is not under maintenance"
    )
    assert server.calls_to(STOP_MAINTENANCE) == []


async def test_a_failing_maintenance_endpoint_only_affects_maintenance(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_maintenance_server(hass, server)
    server.overrides[MAINTENANCE_SESSIONS] = server.error()

    await refresh(hass, entry)

    assert state(hass, f"binary_sensor.{LOCAL_REPO}_maintenance") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{LOCAL_REPO}_maintenance_status") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{LOCAL_REPO}_used_space") != STATE_UNAVAILABLE
    health_ok = hass.states.get(f"binary_sensor.{SERVER}_health_ok")
    assert health_ok.attributes["failed_endpoints"] == ["repository_maintenance"]


@pytest.mark.parametrize("version", ["6", "7"])
async def test_older_versions_have_no_repository_maintenance(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    server.license = {"status": "Valid", "type": "Subscription"}
    await setup_entry(hass, api_version=version)

    assert hass.states.get(f"binary_sensor.{LOCAL_REPO}_maintenance") is None
    assert hass.states.get(f"sensor.{LOCAL_REPO}_maintenance_status") is None
    assert hass.states.get(f"button.{LOCAL_REPO}_start_maintenance") is None
    assert server.calls_to(MAINTENANCE_SESSIONS) == []


@pytest.mark.parametrize("version", ["8.1.0.305", "8.5.0.2000"])
async def test_servers_before_8_6_have_no_repository_maintenance(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    """API v8, but a server that does not serve the endpoint: not asked, and not failing."""
    server.version = version
    entry = await setup_entry(hass)
    await refresh(hass, entry)

    assert hass.states.get(f"binary_sensor.{LOCAL_REPO}_maintenance") is None
    assert hass.states.get(f"sensor.{LOCAL_REPO}_maintenance_status") is None
    assert hass.states.get(f"button.{LOCAL_REPO}_start_maintenance") is None
    assert hass.states.get(f"button.{LOCAL_REPO}_stop_maintenance") is None
    assert server.calls_to(MAINTENANCE_SESSIONS) == []
    assert "repository_maintenance" not in entry.runtime_data["coordinator"].data["fetch_ok"]
    assert state(hass, f"binary_sensor.{SERVER}_health_ok") == STATE_ON


async def test_an_unknown_server_version_counts_as_too_old(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.version = "8.6.0.1004"
    server.overrides["service_instance.service_instance_get"] = server.error()
    await setup_entry(hass)

    assert hass.states.get(f"binary_sensor.{LOCAL_REPO}_maintenance") is None
    assert server.calls_to(MAINTENANCE_SESSIONS) == []


async def test_the_version_is_remembered_when_server_info_later_fails(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_maintenance_server(hass, server)
    server.overrides["service_instance.service_instance_get"] = server.error()

    await refresh(hass, entry)

    assert len(server.calls_to(MAINTENANCE_SESSIONS)) == 2
    assert state(hass, f"binary_sensor.{LOCAL_REPO}_maintenance") == STATE_OFF


# ---------------------------------------------------------------------------
# Job sessions (API v8)
# ---------------------------------------------------------------------------

JOB_SESSIONS = "job_session.job_session_get"
JOB_SESSION = "job_session.job_session_get_by_id"
RUNNING_SESSION = "5e550009-0000-0000-0000-000000000000"


async def test_each_job_shows_its_latest_session(hass: HomeAssistant, server: FakeServer) -> None:
    await setup_entry(hass)

    last = hass.states.get(f"sensor.{JOB}_last_session")
    # The session an hour ago, not the one three hours ago
    assert last.attributes["session_id"] == "5e550002-0000-0000-0000-000000000000"
    assert last.attributes["status"] == "Success"
    assert last.attributes["type"] == "Incremental"
    assert last.attributes["details"] == "All items processed successfully."
    assert last.attributes["bottleneck"] == "Source"
    # 15 minutes, 1 GiB, 120 objects
    assert state(hass, f"sensor.{JOB}_last_session_duration") == "15.0"
    assert state(hass, f"sensor.{JOB}_last_session_transferred") == "1024.0"
    assert state(hass, f"sensor.{JOB}_last_session_processed_objects") == "120"
    assert state(hass, f"sensor.{JOB}_last_session_processing_rate") == "1.0"
    # Copy jobs get theirs too
    copy_last = hass.states.get(f"sensor.{COPY_JOB}_last_session")
    assert copy_last.attributes["raw_value"] == "Warning"


async def test_later_polls_only_ask_for_what_is_new(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    first = [
        c["end_time_lower_bound"]
        for c in server.calls_to(JOB_SESSIONS)
        if "end_time_lower_bound" in c
    ]

    await refresh(hass, entry)

    bounds = [
        c["end_time_lower_bound"]
        for c in server.calls_to(JOB_SESSIONS)
        if "end_time_lower_bound" in c
    ]
    assert len(bounds) == 2
    # The first poll looks back about a day; the next only to shortly before the first
    assert first[0] < dt_util.utcnow() - timedelta(hours=25)
    assert bounds[1] > dt_util.utcnow() - timedelta(minutes=15)
    # The largest pages the server allows; running sessions asked for separately
    assert all(c["limit"] == 10000 for c in server.calls_to(JOB_SESSIONS))
    assert sum("status" in c for c in server.calls_to(JOB_SESSIONS)) == 2


async def test_a_session_that_finished_between_polls_is_read_by_id(
    hass: HomeAssistant, server: FakeServer
) -> None:
    running = job_session_json(RUNNING_SESSION, JOB_ID, 45, status="Running", duration_minutes=None)
    server.job_sessions.append(running)
    entry = await setup_entry(hass)

    last = hass.states.get(f"sensor.{JOB}_last_session")
    assert last.attributes["status"] == "Running"
    # Running for 45 minutes so far
    assert float(state(hass, f"sensor.{JOB}_last_session_duration")) == pytest.approx(45, abs=0.5)

    # It finishes: no longer running, and created before the window the next poll asks for
    running.update(
        status="Failed", endTime=dt_util.utcnow().isoformat(), details="Mailbox not found."
    )
    await refresh(hass, entry)

    assert server.calls_to(JOB_SESSION) == [{"job_sessions_id": RUNNING_SESSION}]
    last = hass.states.get(f"sensor.{JOB}_last_session")
    assert last.attributes["status"] == "Failed"
    assert last.attributes["details"] == "Mailbox not found."


async def test_a_new_session_replaces_the_last_one(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup_entry(hass)
    server.job_sessions.append(
        job_session_json("5e55000a-0000-0000-0000-000000000000", JOB_ID, 1, status="Warning")
    )

    await refresh(hass, entry)

    last = hass.states.get(f"sensor.{JOB}_last_session")
    assert last.attributes["session_id"] == "5e55000a-0000-0000-0000-000000000000"
    assert last.attributes["raw_value"] == "Warning"


async def test_a_job_without_sessions_reads_unknown(
    hass: HomeAssistant, server: FakeServer
) -> None:
    server.job_sessions = []
    await setup_entry(hass)

    assert state(hass, f"sensor.{JOB}_last_session") == STATE_UNKNOWN
    assert state(hass, f"sensor.{JOB}_last_session_duration") == STATE_UNKNOWN


async def test_a_failing_sessions_endpoint_keeps_what_was_known(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = await setup_entry(hass)
    server.overrides[JOB_SESSIONS] = server.error()

    await refresh(hass, entry)

    assert state(hass, f"sensor.{JOB}_last_session") == STATE_UNAVAILABLE
    assert state(hass, f"sensor.{JOB}_last_status") == "Success"
    health_ok = hass.states.get(f"binary_sensor.{SERVER}_health_ok")
    assert health_ok.attributes["failed_endpoints"] == ["job_sessions"]

    del server.overrides[JOB_SESSIONS]
    await refresh(hass, entry)
    last = hass.states.get(f"sensor.{JOB}_last_session")
    assert last.attributes["session_id"] == "5e550002-0000-0000-0000-000000000000"


@pytest.mark.parametrize("version", ["6", "7"])
async def test_older_versions_show_no_sessions(
    hass: HomeAssistant, server: FakeServer, version: str
) -> None:
    server.license = {"status": "Valid", "type": "Subscription"}
    await setup_entry(hass, api_version=version)

    assert hass.states.get(f"sensor.{JOB}_last_session") is None
    assert server.calls_to(JOB_SESSIONS) == []
