"""The shipped blueprints, run by Home Assistant against fake Veeam entities.

test_blueprints.py checks the files; this runs them. Each test creates devices and entities
the way the integration names them ("VB365 Job Daily Mail", ...) without loading the
integration itself, instantiates a blueprint with a notification action that calls a mock
service, and drives state changes, bus events and the clock.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import shutil
from typing import Any

from homeassistant.components import automation
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.veeam_365.const import DOMAIN, EVENT_JOB_SESSION

from .conftest import COPY_JOB_ID, JOB_ID, ORG_ID, OTHER_ORG_ID, PROXY_ID, REPO_ID

BLUEPRINT_DIR = Path(__file__).parent.parent / "blueprints" / "automation" / "veeam_365"
NOTIFY = [{"action": "test.notify", "data": {"title": "{{ title }}", "message": "{{ message }}"}}]
RECOVER = [{"action": "test.recover", "data": {"title": "{{ title }}", "message": "{{ message }}"}}]


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry to hang devices on; the integration itself is never set up."""
    target = Path(hass.config.path("blueprints", "automation", DOMAIN))
    target.mkdir(parents=True, exist_ok=True)
    for path in BLUEPRINT_DIR.glob("*.yaml"):
        shutil.copy(path, target / path.name)

    # Times in these tests are written in UTC
    await hass.config.async_set_time_zone("UTC")

    config_entry = MockConfigEntry(domain=DOMAIN, data={"host": "veeam.example.com"})
    config_entry.add_to_hass(hass)
    yield config_entry

    # Time triggers keep a timer scheduled for as long as their automation is on
    if hass.services.has_service(automation.DOMAIN, "turn_off"):
        await hass.services.async_call(
            automation.DOMAIN, "turn_off", {"entity_id": "all"}, blocking=True
        )
        await hass.async_block_till_done()


@pytest.fixture
def notified(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "test", "notify")


@pytest.fixture
def recovered(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "test", "recover")


def add_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    device: str,
    device_name: str,
    entity_id: str,
    state: str,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Register an entity on a device, as the integration would, and give it a state."""
    device_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device)},
        name=device_name,
    )
    domain, object_id = entity_id.split(".")
    registered = er.async_get(hass).async_get_or_create(
        domain,
        DOMAIN,
        f"{entry.entry_id}_{object_id}",
        suggested_object_id=object_id,
        config_entry=entry,
        device_id=device_entry.id,
    )
    assert registered.entity_id == entity_id
    hass.states.async_set(entity_id, state, attributes or {})
    return entity_id


async def use_blueprint(hass: HomeAssistant, name: str, inputs: dict[str, Any]) -> None:
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "use_blueprint": {"path": f"{DOMAIN}/{name}.yaml", "input": inputs},
            }
        },
    )
    await hass.async_block_till_done()


async def set_state(hass, entity_id, state, attributes=None):
    hass.states.async_set(entity_id, state, attributes or {})
    await hass.async_block_till_done()


async def advance(hass, freezer, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def session_event(hass, job_id, status, session_id="s-1", job_name="Daily Mail"):
    hass.bus.async_fire(
        EVENT_JOB_SESSION,
        {
            "entry_id": "x",
            "job_id": job_id,
            "job_name": job_name,
            "job_type": "Backup",
            "session_id": session_id,
            "status": status,
        },
    )


# ---------------------------------------------------------------------------
# job_failed
# ---------------------------------------------------------------------------


@pytest.fixture
def jobs(hass, entry) -> tuple[str, str]:
    job = add_entity(
        hass, entry, f"job_{JOB_ID}", "VB365 Job Daily Mail", "sensor.daily_mail_status", "Running"
    )
    add_entity(
        hass,
        entry,
        f"job_{JOB_ID}",
        "VB365 Job Daily Mail",
        "sensor.daily_mail_last_session",
        "2026-09-28T05:00:00+00:00",
        {"session_id": "s-1", "details": "Mailbox anna@contoso.com failed", "will_retry": False},
    )
    copy_job = add_entity(
        hass,
        entry,
        f"copy_job_{COPY_JOB_ID}",
        "VB365 Mail Copy Job",
        "sensor.mail_copy_status",
        "Success",
    )
    return job, copy_job


async def test_job_failed_notifies_on_a_status_change(hass, jobs, notified):
    job, copy_job = jobs
    await use_blueprint(
        hass,
        "job_failed",
        {"job_status_sensors": [job, copy_job], "notification_action": NOTIFY},
    )

    await set_state(hass, job, "Failed")

    assert len(notified) == 1
    assert notified[0].data["title"] == "Veeam 365 backup failed: Daily Mail"
    # The session's own message comes from the Last Session sensor on the same device
    assert notified[0].data["message"] == (
        "Veeam 365 job Daily Mail finished with status Failed.\nMailbox anna@contoso.com failed"
    )


async def test_job_failed_reports_an_event_and_its_status_change_once(hass, jobs, notified):
    """The feed fires first; the poll it triggers moves Last Status a moment later."""
    job, copy_job = jobs
    await use_blueprint(
        hass,
        "job_failed",
        {"job_status_sensors": [job, copy_job], "notification_action": NOTIFY},
    )

    session_event(hass, JOB_ID, "Failed")
    await hass.async_block_till_done()
    await set_state(hass, job, "Failed")

    assert len(notified) == 1


async def test_job_failed_status_seen_first_is_not_reported_again_by_the_event(
    hass, jobs, notified
):
    job, copy_job = jobs
    await use_blueprint(
        hass,
        "job_failed",
        {"job_status_sensors": [job, copy_job], "notification_action": NOTIFY},
    )

    await set_state(hass, job, "Failed")
    session_event(hass, JOB_ID, "Failed")
    await hass.async_block_till_done()

    assert len(notified) == 1


async def test_job_failed_event_reports_a_repeat_failure(hass, freezer, jobs, notified):
    """Failed again after Failed leaves Last Status unchanged; only the event sees it."""
    job, copy_job = jobs
    await set_state(hass, job, "Failed")
    await use_blueprint(
        hass,
        "job_failed",
        {"job_status_sensors": [job, copy_job], "notification_action": NOTIFY},
    )
    await advance(hass, freezer, timedelta(hours=1))

    session_event(hass, JOB_ID, "Failed", session_id="s-2")
    await hass.async_block_till_done()

    assert len(notified) == 1
    assert notified[0].data["title"] == "Veeam 365 backup failed: Daily Mail"
    # The Last Session sensor still describes the previous session: its details stay out
    assert "anna@contoso.com" not in notified[0].data["message"]


async def test_job_failed_event_matches_copy_jobs_and_ignores_unwatched_jobs(
    hass, freezer, jobs, notified
):
    job, copy_job = jobs
    await set_state(hass, copy_job, "Failed")
    await use_blueprint(
        hass, "job_failed", {"job_status_sensors": [copy_job], "notification_action": NOTIFY}
    )
    await advance(hass, freezer, timedelta(hours=1))

    session_event(hass, JOB_ID, "Failed")
    await hass.async_block_till_done()
    assert notified == []

    session_event(hass, COPY_JOB_ID, "Failed", job_name="Mail Copy Job")
    await hass.async_block_till_done()
    assert len(notified) == 1
    # "VB365 Mail Copy Job": the kind was already in the name, so only the prefix goes
    assert notified[0].data["title"] == "Veeam 365 backup failed: Mail Copy Job"


async def test_job_failed_warnings_are_opt_in(hass, jobs, notified):
    job, copy_job = jobs
    await use_blueprint(
        hass, "job_failed", {"job_status_sensors": [job], "notification_action": NOTIFY}
    )
    await set_state(hass, job, "Warning")
    assert notified == []


async def test_job_failed_warnings_when_enabled(hass, jobs, notified):
    job, copy_job = jobs
    await use_blueprint(
        hass,
        "job_failed",
        {"job_status_sensors": [job], "include_warnings": True, "notification_action": NOTIFY},
    )
    await set_state(hass, job, "Warning")
    assert len(notified) == 1
    assert notified[0].data["title"] == "Veeam 365 backup warning: Daily Mail"


async def test_job_failed_ignores_a_reload(hass, jobs, notified):
    job, copy_job = jobs
    await set_state(hass, job, "Failed")
    await use_blueprint(
        hass, "job_failed", {"job_status_sensors": [job], "notification_action": NOTIFY}
    )

    await set_state(hass, job, "unavailable")
    await set_state(hass, job, "Failed")

    assert notified == []


async def test_job_failed_keeps_old_unprefixed_device_names(hass, entry, notified):
    job = add_entity(hass, entry, "job_legacy", "Daily Mail", "sensor.legacy_status", "Success")
    await use_blueprint(
        hass, "job_failed", {"job_status_sensors": [job], "notification_action": NOTIFY}
    )
    await set_state(hass, job, "Failed")
    assert notified[0].data["title"] == "Veeam 365 backup failed: Daily Mail"


# ---------------------------------------------------------------------------
# daily_backup_summary
# ---------------------------------------------------------------------------


async def test_daily_summary_names_jobs_without_the_prefix(hass, freezer, jobs, notified):
    job, copy_job = jobs
    await set_state(hass, job, "Failed")
    freezer.move_to("2026-09-28 07:59:00")
    await use_blueprint(
        hass,
        "daily_backup_summary",
        {"job_status_sensors": [job, copy_job], "notification_action": NOTIFY},
    )

    freezer.move_to("2026-09-28 08:00:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    assert len(notified) == 1
    assert "- Daily Mail: Failed" in notified[0].data["message"]


# ---------------------------------------------------------------------------
# repository_offline
# ---------------------------------------------------------------------------


@pytest.fixture
def repository(hass, entry) -> tuple[str, str]:
    accessible = add_entity(
        hass,
        entry,
        f"repository_{REPO_ID}",
        "VB365 Repository Primary",
        "binary_sensor.primary_accessible",
        "on",
        {"device_class": "connectivity"},
    )
    # Cache In Sync keeps the unique ID, and so the entity ID, it had as "Online"
    cache = add_entity(
        hass,
        entry,
        f"repository_{REPO_ID}",
        "VB365 Repository Primary",
        "binary_sensor.primary_online",
        "on",
    )
    return accessible, cache


async def test_repository_offline_and_back(hass, freezer, repository, notified, recovered):
    accessible, cache = repository
    await use_blueprint(
        hass,
        "repository_offline",
        {
            "repository_sensors": [accessible, cache],
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
        },
    )

    await set_state(hass, accessible, "off", {"device_class": "connectivity"})
    await advance(hass, freezer, timedelta(minutes=11))
    assert len(notified) == 1
    assert notified[0].data["title"] == "Veeam 365 repository offline: Primary"

    await set_state(hass, accessible, "on", {"device_class": "connectivity"})
    assert len(recovered) == 1
    assert recovered[0].data["title"] == "Veeam 365 repository back online: Primary"


async def test_repository_blip_raises_neither_alert_nor_recovery(
    hass, freezer, repository, notified, recovered
):
    accessible, cache = repository
    await use_blueprint(
        hass,
        "repository_offline",
        {
            "repository_sensors": [accessible],
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
        },
    )

    await set_state(hass, accessible, "off", {"device_class": "connectivity"})
    await advance(hass, freezer, timedelta(minutes=1))
    await set_state(hass, accessible, "on", {"device_class": "connectivity"})
    await advance(hass, freezer, timedelta(minutes=20))

    assert notified == []
    assert recovered == []


async def test_repository_cache_out_of_sync_says_so(hass, freezer, repository, notified):
    accessible, cache = repository
    await use_blueprint(
        hass,
        "repository_offline",
        {"repository_sensors": [cache], "notification_action": NOTIFY},
    )

    await set_state(hass, cache, "off")
    await advance(hass, freezer, timedelta(minutes=11))

    assert notified[0].data["title"] == "Veeam 365 repository cache out of sync: Primary"


async def test_repository_ignores_unavailable(hass, freezer, repository, notified, recovered):
    accessible, cache = repository
    await use_blueprint(
        hass,
        "repository_offline",
        {
            "repository_sensors": [accessible],
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
        },
    )

    await set_state(hass, accessible, "unavailable")
    await advance(hass, freezer, timedelta(minutes=20))
    await set_state(hass, accessible, "on", {"device_class": "connectivity"})

    assert notified == []
    assert recovered == []


# ---------------------------------------------------------------------------
# proxy_offline
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy(hass, entry) -> tuple[str, str]:
    online = add_entity(
        hass,
        entry,
        f"proxy_{PROXY_ID}",
        "VB365 Proxy proxy01",
        "binary_sensor.proxy01_online",
        "on",
        {"device_class": "connectivity", "raw_value": "Online", "fqdn": "proxy01.example.com"},
    )
    maintenance = add_entity(
        hass,
        entry,
        f"proxy_{PROXY_ID}",
        "VB365 Proxy proxy01",
        "sensor.proxy01_maintenance_mode",
        "Disabled",
        {"raw_value": "Disabled"},
    )
    return online, maintenance


OFFLINE = {"device_class": "connectivity", "raw_value": "Offline"}
ONLINE = {"device_class": "connectivity", "raw_value": "Online"}


async def test_proxy_offline_and_back(hass, freezer, proxy, notified, recovered):
    online, maintenance = proxy
    await use_blueprint(
        hass,
        "proxy_offline",
        {
            "proxy_online_sensors": [online],
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
        },
    )

    await set_state(hass, online, "off", OFFLINE)
    await advance(hass, freezer, timedelta(minutes=6))
    assert len(notified) == 1
    assert notified[0].data["title"] == "Veeam 365 proxy offline: proxy01"

    await set_state(hass, online, "on", ONLINE)
    assert len(recovered) == 1


async def test_proxy_in_maintenance_is_skipped(hass, freezer, proxy, notified):
    online, maintenance = proxy
    await use_blueprint(
        hass,
        "proxy_offline",
        {"proxy_online_sensors": [online], "notification_action": NOTIFY},
    )

    await set_state(hass, maintenance, "Enabled", {"raw_value": "Enabled"})
    await set_state(hass, online, "off", OFFLINE)
    await advance(hass, freezer, timedelta(minutes=6))

    assert notified == []


async def test_proxy_in_maintenance_when_not_ignored(hass, freezer, proxy, notified):
    online, maintenance = proxy
    await use_blueprint(
        hass,
        "proxy_offline",
        {
            "proxy_online_sensors": [online],
            "ignore_maintenance": False,
            "notification_action": NOTIFY,
        },
    )

    await set_state(hass, maintenance, "Enabled", {"raw_value": "Enabled"})
    await set_state(hass, online, "off", OFFLINE)
    await advance(hass, freezer, timedelta(minutes=6))

    assert len(notified) == 1


async def test_proxy_blip_gets_no_recovery(hass, freezer, proxy, notified, recovered):
    online, maintenance = proxy
    await use_blueprint(
        hass,
        "proxy_offline",
        {
            "proxy_online_sensors": [online],
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
        },
    )

    await set_state(hass, online, "off", OFFLINE)
    await advance(hass, freezer, timedelta(minutes=1))
    await set_state(hass, online, "on", ONLINE)

    assert notified == []
    assert recovered == []


# ---------------------------------------------------------------------------
# server_health
# ---------------------------------------------------------------------------


@pytest.fixture
def server(hass, entry) -> tuple[str, str, str]:
    device = f"server_{entry.entry_id}"
    name = "VB365 Server veeam.example.com"
    health_ok = add_entity(
        hass,
        entry,
        device,
        name,
        "binary_sensor.server_health_ok",
        "on",
        {"device_class": "running", "failed_endpoints": []},
    )
    connected = add_entity(
        hass,
        entry,
        device,
        name,
        "binary_sensor.server_connected",
        "on",
        {"device_class": "connectivity"},
    )
    service = add_entity(
        hass,
        entry,
        device,
        name,
        "binary_sensor.server_service_health",
        "off",
        {"device_class": "problem", "raw_value": "Healthy", "problems": []},
    )
    return health_ok, connected, service


async def test_server_health_ignores_a_single_failed_poll(hass, freezer, server, notified):
    health_ok, connected, service = server
    await use_blueprint(
        hass,
        "server_health",
        {"health_ok_sensors": [health_ok], "notification_action": NOTIFY},
    )

    await set_state(
        hass, health_ok, "off", {"device_class": "running", "failed_endpoints": ["proxies"]}
    )
    await advance(hass, freezer, timedelta(minutes=1))
    await set_state(hass, health_ok, "on", {"device_class": "running", "failed_endpoints": []})
    await advance(hass, freezer, timedelta(minutes=10))

    # Neither the problem nor a recovery from a problem nobody was told about
    assert notified == []


async def test_server_health_problem_and_recovery(hass, freezer, server, notified):
    health_ok, connected, service = server
    await use_blueprint(
        hass,
        "server_health",
        {"health_ok_sensors": [health_ok], "notification_action": NOTIFY},
    )

    await set_state(
        hass,
        health_ok,
        "off",
        {"device_class": "running", "failed_endpoints": ["organization_sync", "proxies"]},
    )
    await advance(hass, freezer, timedelta(minutes=6))
    assert len(notified) == 1
    assert notified[0].data["title"] == "Veeam 365 server health problem: veeam.example.com"
    assert "organization sync, proxies" in notified[0].data["message"]

    await set_state(hass, health_ok, "on", {"device_class": "running", "failed_endpoints": []})
    assert len(notified) == 2
    assert notified[1].data["title"] == "Veeam 365 server healthy again: veeam.example.com"


async def test_server_health_recovery_can_be_turned_off(hass, freezer, server, notified):
    health_ok, connected, service = server
    await use_blueprint(
        hass,
        "server_health",
        {
            "health_ok_sensors": [health_ok],
            "notify_on_recovery": False,
            "notification_action": NOTIFY,
        },
    )

    await set_state(hass, health_ok, "off", {"device_class": "running", "failed_endpoints": []})
    await advance(hass, freezer, timedelta(minutes=6))
    await set_state(hass, health_ok, "on", {"device_class": "running", "failed_endpoints": []})

    assert len(notified) == 1


async def test_server_health_unreachable_server(hass, freezer, server, notified):
    health_ok, connected, service = server
    await use_blueprint(
        hass,
        "server_health",
        {"health_ok_sensors": [health_ok], "notification_action": NOTIFY},
    )

    await set_state(hass, connected, "off", {"device_class": "connectivity"})
    await set_state(hass, health_ok, "off", {"device_class": "running", "failed_endpoints": []})
    await advance(hass, freezer, timedelta(minutes=6))

    assert notified[0].data["message"] == "The server has not been answering for a while."


async def test_server_health_service_health(hass, freezer, server, notified):
    health_ok, connected, service = server
    await use_blueprint(
        hass,
        "server_health",
        {
            "health_ok_sensors": [health_ok],
            "service_health_sensors": [service],
            "notification_action": NOTIFY,
        },
    )

    await set_state(
        hass,
        service,
        "on",
        {"device_class": "problem", "raw_value": "Unhealthy", "problems": ["NATS server"]},
    )
    await advance(hass, freezer, timedelta(minutes=6))

    assert notified[0].data["title"] == "Veeam 365 server unhealthy: veeam.example.com"
    assert "Failing: NATS server." in notified[0].data["message"]


# ---------------------------------------------------------------------------
# organization_not_backed_up
# ---------------------------------------------------------------------------


@pytest.fixture
def organizations(hass, entry) -> tuple[str, str, str]:
    contoso = add_entity(
        hass,
        entry,
        f"organization_{ORG_ID}",
        "VB365 Organization contoso.onmicrosoft.com",
        "sensor.contoso_last_backup",
        "2026-09-27T06:00:00+00:00",
        {"device_class": "timestamp"},
    )
    backed_up = add_entity(
        hass,
        entry,
        f"organization_{ORG_ID}",
        "VB365 Organization contoso.onmicrosoft.com",
        "binary_sensor.contoso_backed_up",
        "on",
    )
    never = add_entity(
        hass,
        entry,
        f"organization_{OTHER_ORG_ID}",
        "VB365 Organization fabrikam.onmicrosoft.com",
        "sensor.fabrikam_last_backup",
        "unknown",
        {"device_class": "timestamp"},
    )
    return contoso, backed_up, never


async def fire_check(hass, freezer, when: str) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_organization_overdue_is_reported_once_then_recovers(
    hass, freezer, organizations, notified, recovered
):
    contoso, backed_up, never = organizations
    freezer.move_to("2026-09-28 07:50:00+00:00")
    await use_blueprint(
        hass,
        "organization_not_backed_up",
        {
            "last_backup_sensors": [contoso, never],
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
        },
    )

    # 26 hours after the last backup is 08:00: not overdue at 07:45's check...
    await fire_check(hass, freezer, "2026-09-28 07:45:00+00:00")
    assert notified == []

    # ...overdue at 08:15's, and reported once
    await fire_check(hass, freezer, "2026-09-28 08:15:00+00:00")
    assert len(notified) == 1
    assert notified[0].data["title"] == (
        "Veeam 365: contoso.onmicrosoft.com not backed up for 26 hours"
    )
    # The organization that was never backed up reads unknown, and is skipped
    assert notified[0].data["message"] == (
        "contoso.onmicrosoft.com: last backup 26 hours ago (2026-09-27 06:00)."
    )

    await fire_check(hass, freezer, "2026-09-28 08:30:00+00:00")
    await fire_check(hass, freezer, "2026-09-28 09:00:00+00:00")
    assert len(notified) == 1

    await set_state(hass, contoso, "2026-09-28 09:05:00+00:00", {"device_class": "timestamp"})
    assert len(recovered) == 1
    assert recovered[0].data["title"] == "Veeam 365 backups resumed: contoso.onmicrosoft.com"
    assert len(notified) == 1


async def test_organization_backed_up_turning_off(hass, freezer, organizations, notified):
    contoso, backed_up, never = organizations
    freezer.move_to("2026-09-27 12:00:00+00:00")
    await use_blueprint(
        hass,
        "organization_not_backed_up",
        {
            "last_backup_sensors": [contoso],
            "backed_up_sensors": [backed_up],
            "notification_action": NOTIFY,
        },
    )

    await set_state(hass, backed_up, "off")

    assert len(notified) == 1
    assert notified[0].data["title"] == "Veeam 365: contoso.onmicrosoft.com no longer backed up"


async def test_organization_backed_up_ignores_unavailable(hass, freezer, organizations, notified):
    contoso, backed_up, never = organizations
    freezer.move_to("2026-09-27 12:00:00+00:00")
    await use_blueprint(
        hass,
        "organization_not_backed_up",
        {
            "last_backup_sensors": [contoso],
            "backed_up_sensors": [backed_up],
            "notification_action": NOTIFY,
        },
    )

    await set_state(hass, backed_up, "unavailable")
    await set_state(hass, backed_up, "off")
    await set_state(hass, contoso, "unavailable")
    await fire_check(hass, freezer, "2026-09-29 12:00:00+00:00")

    assert notified == []


# ---------------------------------------------------------------------------
# organization_sync_failed
# ---------------------------------------------------------------------------


async def test_organization_sync_failed(hass, entry, notified, recovered):
    sync = add_entity(
        hass,
        entry,
        f"organization_{ORG_ID}",
        "VB365 Organization contoso.onmicrosoft.com",
        "binary_sensor.contoso_sync",
        "off",
        {"device_class": "problem"},
    )
    await use_blueprint(
        hass,
        "organization_sync_failed",
        {"sync_sensors": [sync], "notification_action": NOTIFY, "recovery_action": RECOVER},
    )

    # Coming back from unavailable (a reload) already failing is not a new failure
    await set_state(hass, sync, "unavailable")
    await set_state(hass, sync, "on", {"device_class": "problem", "error": "Access denied"})
    assert notified == []
    await set_state(hass, sync, "unavailable")
    await set_state(hass, sync, "off", {"device_class": "problem"})
    assert recovered == []

    await set_state(hass, sync, "on", {"device_class": "problem", "error": "Access denied"})
    assert len(notified) == 1
    assert notified[0].data["title"] == (
        "Veeam 365 organization sync failed: contoso.onmicrosoft.com"
    )
    assert "failed: Access denied." in notified[0].data["message"]

    await set_state(hass, sync, "off", {"device_class": "problem"})
    assert len(recovered) == 1
