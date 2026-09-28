"""Polling the Veeam Backup for Microsoft 365 REST API.

Error handling, in one place:

* ``VeeamAuthenticationError`` — the password login was refused. The SDK has already fallen
  back from the refresh grant to the password, so this really is the credentials:
  ``ConfigEntryAuthFailed`` starts the reauth flow.
* ``VeeamSessionError`` — the server rejected the session mid-use. The SDK has dropped it and
  the next call logs in again, so the poll is retried once straight away (every call here
  is a read, so retrying is safe). A second rejection fails the update; the next poll
  starts from a fresh login again.
* Transport errors and timeouts — the server is unreachable: ``UpdateFailed``.
* An endpoint that answers with an error, or with something that does not parse — that
  endpoint is marked failed in ``data["fetch_ok"]`` and keeps its previous data, so its
  entities go unavailable instead of silently reporting an empty collection, and nothing is
  pruned on its account. When the jobs endpoint fails, or every endpoint does, the update
  fails as a whole.
* One item that does not parse is skipped and logged; the rest of its endpoint survives.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from enum import Enum
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
import httpx
from veeam_365.exceptions import VeeamAuthenticationError, VeeamError, VeeamSessionError

from .const import (
    DOMAIN,
    JOB_SESSIONS_LOOKBACK_HOURS,
    JOB_SESSIONS_OVERLAP_MINUTES,
    JOB_SESSIONS_PAGE_LIMIT,
    MAX_PAGES,
    PAGE_LIMIT,
    PROTECTED_COUNT_INTERVAL,
    PROTECTED_COUNT_TIMEOUT,
    PROTECTED_PAGE_LIMIT,
    UPDATE_INTERVAL,
    UPDATE_TIMEOUT,
)
from .display import humanize
from .licensing import describe_license, unsupported_license_reason
from .sdk import VeeamSdk

_LOGGER = logging.getLogger(__name__)

# Coordinator data keys for each endpoint, and what an endpoint holds when it has never
# been fetched successfully
ENDPOINT_DEFAULTS: dict[str, Any] = {
    "jobs": [],
    "copy_jobs": [],
    "server_info": None,
    "license_info": None,
    "repositories": [],
    "proxies": [],
    "organizations": [],
    "organization_sync": {},
    "repository_maintenance": {},
    "job_sessions": {},
    "health": None,
}

# The server's own health report (NATS and the configuration database). API v8 only; on
# older versions it is not fetched at all rather than reported as a failing endpoint.
HEALTH_OPERATION = "health.health_get"

# Organization cache synchronization state: every organization in one call from v8, one
# call per organization on v7, not at all on v6
SYNC_STATES_OPERATION = "organization_sync.organization_sync_get_states"
SYNC_STATE_OPERATION = "organization_sync.organization_sync_get_state"
LICENSING_OPERATION = (
    "organization_licensing_information.organization_licensing_information_get_license_count"
)


# What an organization protects, by the kind reported and the operation listing it (v8)
PROTECTED_OPERATIONS: dict[str, str] = {
    "users": "protected_data.protected_data_get_protected_users",
    "groups": "protected_data.protected_data_get_protected_groups",
    "sites": "protected_data.protected_data_get_protected_sites",
    "teams": "protected_data.protected_data_get_protected_teams",
}


# Repository maintenance sessions (v8): VB365 suspends every operation on the repositories a
# session names while it is active
MAINTENANCE_SESSIONS_OPERATION = (
    "repository_maintenance_session.repository_maintenance_sessions_get"
)
ACTIVE_MAINTENANCE_STATUSES = frozenset(
    {"Initialized", "Preparing", "Running", "Finishing", "Canceling", "Failing"}
)


# Job sessions (v8). Every version lists them, but only v8 says which job a session belongs
# to and filters by status, which is what makes finding each job's latest session cheap.
JOB_SESSIONS_OPERATION = "job_session.job_session_get"
JOB_SESSION_OPERATION = "job_session.job_session_get_by_id"
RUNNING_SESSION_STATUSES = frozenset({"Running", "Queued"})


def supports_job_sessions(sdk: VeeamSdk) -> bool:
    return sdk.accepts(JOB_SESSIONS_OPERATION, "status")


def supports_repository_maintenance(sdk: VeeamSdk) -> bool:
    return sdk.has_operation(MAINTENANCE_SESSIONS_OPERATION)


def supports_protected_counts(sdk: VeeamSdk) -> bool:
    """Whether this API version lists protected objects (v8)."""
    return all(sdk.has_operation(operation) for operation in PROTECTED_OPERATIONS.values())


def supports_organization_sync(sdk: VeeamSdk) -> bool:
    """Whether this API version reports organization sync state at all (v7 and later)."""
    return sdk.has_operation(SYNC_STATES_OPERATION) or sdk.has_operation(SYNC_STATE_OPERATION)


def reports_sync_progress(sdk: VeeamSdk) -> bool:
    """Whether it also reports the sync in progress or queued, and each part (v8)."""
    return sdk.has_operation(SYNC_STATES_OPERATION)


# Collections whose items become devices, keyed by data key
COLLECTIONS = ("jobs", "copy_jobs", "repositories", "proxies", "organizations")

# Errors that mean the server could not be reached or did not answer in time
TRANSPORT_ERRORS = (httpx.HTTPError, OSError, TimeoutError)

# Errors raised while parsing a response or an item of one. JSONDecodeError is a
# ValueError, but the SDK turns that into VeeamSessionError before it gets here.
PARSE_ERRORS = (TypeError, ValueError, KeyError, AttributeError)

BYTES_PER_GIB = 1024**3


class EndpointError(Exception):
    """An endpoint answered, but with an error or something that is not its data."""


# ---------------------------------------------------------------------------
# Reading generated models
# ---------------------------------------------------------------------------


def is_missing(value: Any) -> bool:
    """Whether a model field is absent: None, or the generated UNSET sentinel."""
    return value is None or type(value).__name__ == "Unset"


def clean(value: Any, default: Any = None) -> Any:
    """A model field as a plain value: enums to their value, None/UNSET to ``default``.

    getattr(obj, name, default) does not do this: an absent field is UNSET, not missing, so
    the default never applies and UNSET leaks into sensor states.
    """
    if is_missing(value):
        return default
    if isinstance(value, Enum):
        return value.value
    return value


def field(obj: Any, name: str, default: Any = None) -> Any:
    """``clean(getattr(obj, name))`` — tolerant of fields an API version does not have."""
    return clean(getattr(obj, name, None), default)


def text_field(obj: Any, name: str, default: str | None = None) -> str | None:
    value = field(obj, name)
    if value is None:
        return default
    return str(value)


def id_field(obj: Any, name: str = "id") -> str | None:
    """An ID as a string. The SDK parses IDs as uuid.UUID, which never equals a string."""
    value = field(obj, name)
    if value is None or value == "":
        return None
    return str(value)


def bool_field(obj: Any, name: str) -> bool | None:
    value = field(obj, name)
    return None if value is None else bool(value)


def int_field(obj: Any, name: str) -> int | None:
    value = field(obj, name)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def first_field(obj: Any, *names: str) -> Any:
    """The first of several field names that is present — names differ between versions."""
    for name in names:
        value = field(obj, name)
        if value is not None:
            return value
    return None


def is_error_response(response: Any) -> bool:
    """Whether an operation returned the server's error model instead of data."""
    return type(response).__name__ == "RESTExceptionInfo"


def error_message(response: Any) -> str:
    """Describe an error model for a log line or an exception message."""
    message = text_field(response, "message")
    code = text_field(response, "error_code")
    if message and code:
        return f"{message} ({code})"
    return message or code or "the server returned an error without a message"


def describe_error(err: BaseException) -> str:
    """An exception for a log line. Some (TimeoutError, httpx errors) have no message."""
    text = str(err)
    return f"{type(err).__name__}: {text}" if text else repr(err)


def collection_items(response: Any) -> list[Any]:
    """The items of a collection response.

    v6 and v7 return a plain list; v8 returns a page object with the items in .results.
    """
    if isinstance(response, list):
        return response
    results = getattr(response, "results", None)
    if isinstance(results, list):
        return results
    raise EndpointError(f"expected a collection, got {type(response).__name__}")


def _gib(value: int | None) -> float | None:
    return None if value is None else round(value / BYTES_PER_GIB, 2)


# ---------------------------------------------------------------------------
# Turning models into coordinator data
# ---------------------------------------------------------------------------


def parse_job(job: Any) -> dict[str, Any] | None:
    job_id = id_field(job)
    if job_id is None:
        return None
    backup_type = text_field(job, "backup_type")
    last_status = text_field(job, "last_status")
    # Both the readable label and the untouched API value: sensors show the first,
    # templates and automations match on the second
    return {
        "id": job_id,
        "name": text_field(job, "name", "Unknown Job"),
        "backup_type": humanize(backup_type, "Unknown"),
        "backup_type_raw": backup_type,
        "last_run": field(job, "last_run"),
        "next_run": field(job, "next_run"),
        # v6 does not report the last backup
        "last_backup": field(job, "last_backup"),
        "is_enabled": bool_field(job, "is_enabled"),
        "last_status": humanize(last_status, "Unknown"),
        "last_status_raw": last_status,
    }


def parse_copy_job(copy_job: Any) -> dict[str, Any] | None:
    copy_job_id = id_field(copy_job)
    if copy_job_id is None:
        return None
    last_status = text_field(copy_job, "last_status")
    return {
        "id": copy_job_id,
        "name": text_field(copy_job, "name", "Unknown Copy Job"),
        "backup_job_id": id_field(copy_job, "backup_job_id"),
        "last_run": field(copy_job, "last_run"),
        "last_backup": field(copy_job, "last_backup"),
        "is_enabled": bool_field(copy_job, "is_enabled"),
        "last_status": humanize(last_status, "Unknown"),
        "last_status_raw": last_status,
    }


def parse_repository(repo: Any) -> dict[str, Any] | None:
    repo_id = id_field(repo)
    if repo_id is None:
        return None

    # v8 nests the object storage; v6 and v7 only say whether there is one
    object_storage = field(repo, "object_storage")
    is_object_storage = (
        object_storage is not None or id_field(repo, "object_storage_id") is not None
    )

    capacity = int_field(repo, "capacity_bytes")
    free = int_field(repo, "free_space_bytes")
    used: int | None = None
    is_immutable = False
    immutability_days = None

    if object_storage is not None:
        storage_type = text_field(object_storage, "type_", "ObjectStorage")
        used = int_field(object_storage, "used_space_bytes")
        is_immutable = bool(field(object_storage, "enable_immutability", False))
        immutability_days = int_field(object_storage, "immutability_period_days")
    elif is_object_storage:
        storage_type = "ObjectStorage"
    else:
        storage_type = "Local"
        # A local (JET-based) repository reports its size and free space, not its usage
        if capacity is not None and free is not None:
            used = max(capacity - free, 0)

    retention_type = text_field(repo, "retention_type")
    is_out_of_sync = bool_field(repo, "is_out_of_sync")
    is_out_of_order = bool_field(repo, "is_out_of_order")

    return {
        "id": repo_id,
        "name": text_field(repo, "name", "Unknown Repository"),
        "description": text_field(repo, "description", ""),
        "path": text_field(repo, "path", ""),
        "type": humanize(storage_type, "Unknown"),
        "type_raw": storage_type,
        "retention_type": humanize(retention_type, "Unknown"),
        "retention_type_raw": retention_type,
        "used_space_bytes": used,
        "used_space_gib": _gib(used),
        # For an object storage repository these describe its local cache
        "capacity_bytes": capacity,
        "free_space_bytes": free,
        "is_long_term": bool(field(repo, "is_long_term", False)),
        "is_immutable": is_immutable,
        "immutability_days": immutability_days,
        # Fields only v8 reports are None on older versions, so their sensors read unknown
        # rather than a guess
        "is_outdated": bool_field(repo, "is_outdated"),
        "is_out_of_sync": is_out_of_sync,
        "is_indexed": bool_field(repo, "is_indexed"),
        "is_out_of_order": is_out_of_order,
        "out_of_order_reason": text_field(repo, "out_of_order_reason"),
        # Derived for the binary sensors. "Cache in sync" is what is_out_of_sync reports —
        # it used to be presented as "Online". "Accessible" is the repository not being in
        # the Invalid state.
        "is_cache_in_sync": None if is_out_of_sync is None else not is_out_of_sync,
        "is_accessible": None if is_out_of_order is None else not is_out_of_order,
    }


def _percent(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def parse_proxy(proxy: Any) -> dict[str, Any] | None:
    proxy_id = id_field(proxy)
    if proxy_id is None:
        return None
    status = text_field(proxy, "status")
    maintenance = text_field(proxy, "maintenance_mode_state")
    operating_system = text_field(proxy, "operating_system")
    roles = field(proxy, "role") or []
    return {
        "id": proxy_id,
        # v7 and later also report the FQDN; the host name is what the console shows
        "name": first_field(proxy, "host_name", "fqdn") or "Unknown Proxy",
        "fqdn": text_field(proxy, "fqdn"),
        "description": text_field(proxy, "description", ""),
        "port": int_field(proxy, "port"),
        "is_online": None if status is None else status == "Online",
        "status_raw": status,
        # Whether this API version reports maintenance mode, usage and version at all (v8).
        # Read from the model rather than the values, so a v8 proxy that is offline and
        # reports none of them still gets its sensors, and older versions get none.
        "reports_details": hasattr(proxy, "maintenance_mode_state"),
        "maintenance_mode": humanize(maintenance, "Unknown"),
        "maintenance_mode_raw": maintenance,
        "cpu_usage_percent": _percent(field(proxy, "cpu_usage_percent")),
        "memory_usage_percent": _percent(field(proxy, "memory_usage_percent")),
        "version": text_field(proxy, "version"),
        "operating_system": humanize(operating_system, "Unknown"),
        "operating_system_raw": operating_system,
        "proxy_pool_id": id_field(proxy, "proxy_pool_id"),
        "roles": [str(clean(role)) for role in roles if not is_missing(role)],
    }


def parse_organization(org: Any, licensing: Any = None) -> dict[str, Any] | None:
    org_id = id_field(org)
    if org_id is None:
        return None
    org_type = text_field(org, "type_")
    region = text_field(org, "region")
    services = [
        service
        for service, flags in (
            ("Exchange", ("is_exchange_online", "is_exchange")),
            ("SharePoint", ("is_share_point_online", "is_sharepoint")),
            ("Teams", ("is_teams_online",)),
            ("Teams Chats", ("is_teams_chats_online",)),
        )
        if any(field(org, flag) for flag in flags)
    ]
    return {
        "id": org_id,
        "name": text_field(org, "name") or text_field(org, "office_name") or "Unknown Organization",
        "office_name": text_field(org, "office_name"),
        "description": text_field(org, "description", ""),
        "type": humanize(org_type, "Unknown"),
        "type_raw": org_type,
        "region": humanize(region, "Unknown"),
        "region_raw": region,
        "services": services,
        "is_backed_up": bool_field(org, "is_backedup"),
        "first_backup": field(org, "first_backuptime"),
        "last_backup": field(org, "last_backuptime"),
        # From the organization's licensing information; None when it could not be read
        "licensed_users": int_field(licensing, "licensed_users") if licensing else None,
        "new_users": int_field(licensing, "new_users") if licensing else None,
    }


def _sync_result(value: str | None) -> str | None:
    # v7 reports "None" for an organization that has never been synchronized
    return None if value in (None, "None") else value


def parse_sync_state(state: Any) -> dict[str, Any]:
    """One organization's cache synchronization state, from v7's or v8's shape.

    v7 reports the last run only (type, status, lastSyncTime, error). v8 nests it under
    lastSyncState, adds the run in progress or queued (currentSyncState), and breaks it down
    per part (users, groups, group members, sites).
    """
    last = field(state, "last_sync_state")
    if last is None and not hasattr(state, "last_sync_state"):
        # v7
        result = _sync_result(text_field(state, "status"))
        sync_type = text_field(state, "type_")
        return {
            "last_result": result,
            "has_sync_error": None if result is None else result == "Error",
            "last_sync": field(state, "last_sync_time"),
            "last_sync_type": humanize(sync_type) if sync_type else None,
            "error": text_field(state, "error"),
            "reports_current": False,
            "current_status": None,
            "current_status_raw": None,
            "next_sync": None,
            "parts": {},
        }

    result = _sync_result(text_field(last, "result")) if last is not None else None
    sync_type = text_field(last, "type_") if last is not None else None
    current = field(state, "current_sync_state")
    current_status = text_field(current, "status") if current is not None else None
    parts: dict[str, dict[str, Any]] = {}
    parts_model = field(state, "parts")
    for part in ("users", "groups", "group_members", "sites"):
        part_state = field(parts_model, part) if parts_model is not None else None
        if part_state is None:
            continue
        part_last = field(part_state, "last_sync_state")
        parts[part] = {
            "last_successful_sync": field(part_state, "last_successful_sync_time"),
            "last_result": _sync_result(text_field(part_last, "result")) if part_last else None,
        }
    return {
        "last_result": result,
        "has_sync_error": None if result is None else result == "Error",
        "last_sync": field(last, "end_time") if last is not None else None,
        "last_sync_type": humanize(sync_type) if sync_type else None,
        "error": text_field(last, "error") if last is not None else None,
        "reports_current": True,
        # Nothing queued or running reads as Idle rather than unknown
        "current_status": humanize(current_status) if current_status else "Idle",
        "current_status_raw": current_status,
        "next_sync": field(current, "scheduled_time") if current is not None else None,
        "parts": parts,
    }


def parse_job_session(session: Any, now: Any = None) -> dict[str, Any] | None:
    session_id = id_field(session)
    job_id = id_field(session, "job_id")
    if session_id is None or job_id is None:
        return None
    status = text_field(session, "status")
    config_type = text_field(session, "job_session_config_type")
    created = field(session, "creation_time")
    ended = field(session, "end_time")
    is_running = status in RUNNING_SESSION_STATUSES
    # A running session's duration so far; a finished one's in full
    until = ended or (now if is_running else None)
    duration = (until - created).total_seconds() if created and until else None
    statistics = field(session, "statistics")
    return {
        "session_id": session_id,
        "job_id": job_id,
        "status": humanize(status, "Unknown"),
        "status_raw": status,
        "is_running": is_running,
        "type": humanize(config_type) if config_type else None,
        "creation_time": created,
        "end_time": ended,
        "duration_seconds": None if duration is None else max(round(duration), 0),
        "details": text_field(session, "details"),
        "retry_count": int_field(session, "retry_count"),
        "will_retry": bool_field(session, "job_will_be_retried"),
        "transferred_bytes": int_field(statistics, "transferred_data_bytes"),
        "processed_objects": int_field(statistics, "processed_objects"),
        "processing_rate_bytes_per_second": int_field(statistics, "processing_rate_bytes_ps"),
        "bottleneck": text_field(statistics, "bottleneck"),
    }


def _newer(candidate: dict[str, Any], current: dict[str, Any] | None) -> bool:
    """Whether a session should replace the one known for its job."""
    if current is None or candidate["session_id"] == current["session_id"]:
        return True
    new, old = candidate.get("creation_time"), current.get("creation_time")
    return new is not None and (old is None or new > old)


def parse_maintenance_session(session: Any) -> dict[str, Any] | None:
    session_id = id_field(session)
    if session_id is None:
        return None
    status = text_field(session, "status")
    return {
        "session_id": session_id,
        "status": humanize(status, "Unknown"),
        "status_raw": status,
        "is_active": status in ACTIVE_MAINTENANCE_STATUSES,
        "start_time": field(session, "start_time"),
        "end_time": field(session, "end_time"),
        "error": text_field(session, "error_message"),
        "repository_ids": [
            str(repo_id) for repo_id in field(session, "repository_ids") or [] if repo_id
        ],
    }


def maintenance_by_repository(sessions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each repository's session that matters: an active one, else the latest one."""

    def rank(session: dict[str, Any]) -> tuple[bool, float]:
        start = session.get("start_time")
        return (session["is_active"], start.timestamp() if start else float("-inf"))

    by_repository: dict[str, dict[str, Any]] = {}
    for session in sessions:
        for repo_id in session["repository_ids"]:
            current = by_repository.get(repo_id)
            if current is None or rank(session) > rank(current):
                by_repository[repo_id] = session
    return by_repository


def parse_server_info(service_instance: Any) -> dict[str, Any]:
    return {
        "installation_id": id_field(service_instance, "installation_id"),
        "version": text_field(service_instance, "version"),
    }


def parse_license(license_data: Any, auto_update: Any) -> dict[str, Any]:
    status = text_field(license_data, "status")
    license_type = text_field(license_data, "type_")
    return {
        "status": humanize(status, "Unknown"),
        "status_raw": status,
        "type": humanize(license_type, "Unknown"),
        "type_raw": license_type,
        # v6 calls it expiration_date and has no grace period or package
        "expiration_date": first_field(license_data, "license_expires", "expiration_date"),
        "grace_period_expires": field(license_data, "grace_period_expires"),
        "licensed_to": text_field(license_data, "licensed_to"),
        "email": text_field(license_data, "email"),
        "package": text_field(license_data, "package"),
        "total_number": int_field(license_data, "total_number"),
        "used_number": int_field(license_data, "used_number"),
        "new_number": int_field(license_data, "new_number"),
        "auto_update_enabled": (
            bool_field(auto_update, "is_enabled") if auto_update is not None else None
        ),
    }


def parse_health(report: Any) -> dict[str, Any]:
    """The /Health report: an overall status, and one entry per check (nats, database)."""
    status = text_field(report, "status")
    entries = field(report, "entries")
    checks: dict[str, dict[str, Any]] = {}
    for name, entry in (getattr(entries, "additional_properties", None) or {}).items():
        checks[str(name)] = {
            "status": text_field(entry, "status"),
            "description": text_field(entry, "description"),
        }
    return {
        "status": humanize(status, "Unknown"),
        "status_raw": status,
        # None when the server sends no status, so the binary sensor reads unknown
        "is_healthy": None if status is None else status == "Healthy",
        "checks": checks,
    }


def health_report_from_error(response: Any, models: Any) -> Any | None:
    """A health report the server sent with an error status code, if that is what it is.

    Health endpoints commonly answer 503 while unhealthy. The generated client parses any
    non-200 body as the error model, which keeps unknown keys in additional_properties —
    so an Unhealthy report arrives as a RESTExceptionInfo carrying status and entries.
    Reading it back keeps the sensor saying "Problem" instead of going unavailable exactly
    when it matters.
    """
    body = getattr(response, "additional_properties", None) or {}
    if "status" not in body or text_field(response, "message"):
        return None
    return models.RESTHealthReport.from_dict(body)


def parse_items(
    kind: str, items: list[Any], parser: Callable[[Any], dict[str, Any] | None]
) -> list[dict[str, Any]]:
    """Parse each item on its own, so one malformed item does not take the rest with it."""
    parsed: list[dict[str, Any]] = []
    for item in items:
        try:
            result = parser(item)
        except PARSE_ERRORS as err:
            _LOGGER.warning(
                "Skipping one of the %s the server reported: %s", kind, describe_error(err)
            )
            continue
        if result is None:
            _LOGGER.debug("Skipping one of the %s: it has no ID", kind)
            continue
        parsed.append(result)
    return parsed


def current_ids(data: dict[str, Any] | None, key: str) -> set[str]:
    """IDs of the items in one collection, as strings."""
    return {str(item["id"]) for item in (data or {}).get(key) or [] if item.get("id")}


def fetch_succeeded(data: dict[str, Any] | None, key: str) -> bool:
    """Whether the endpoint behind ``key`` answered on the last poll."""
    return bool(((data or {}).get("fetch_ok") or {}).get(key, False))


def is_prunable(data: dict[str, Any] | None, key: str) -> bool:
    """Whether a collection can be trusted to say what no longer exists.

    Only when its fetch succeeded this cycle and returned something: an empty collection
    is indistinguishable from a degraded fetch, so deleting the last object of a kind is
    left to the device's Delete button.
    """
    return fetch_succeeded(data, key) and bool(current_ids(data, key))


# ---------------------------------------------------------------------------
# The coordinator
# ---------------------------------------------------------------------------


def license_issue_id(entry: ConfigEntry) -> str:
    """Repair issue ID for one config entry's license warning."""
    return f"unsupported_license_{entry.entry_id}"


def check_license_support(hass: HomeAssistant, entry: ConfigEntry, data: dict | None) -> None:
    """Warn when the server's license is outside what this integration supports.

    Raised as a repair issue rather than only a log line, so it is visible without digging
    through logs, and cleared automatically once the server reports a supported license.
    Never blocks setup: a Community Edition server that works is not worth refusing.
    """
    license_info = (data or {}).get("license_info")
    reason = unsupported_license_reason(license_info)
    issue_id = license_issue_id(entry)

    if reason is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    _LOGGER.warning(
        "Veeam server %s reports license %s, which this integration does not support (%s). "
        "Setup will continue, but entities may be missing or unreliable. Please include the "
        "license type when reporting problems",
        entry.data.get(CONF_HOST, "unknown"),
        describe_license(license_info),
        reason,
    )

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unsupported_license",
        translation_placeholders={
            "host": str(entry.data.get(CONF_HOST, "unknown")),
            "license": describe_license(license_info),
        },
    )


class _VeeamCalls:
    """Calling operations and paging through collections, shared by both coordinators."""

    client: Any
    sdk: VeeamSdk

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        """Call one operation; raise EndpointError when it answers with an error."""
        response = await self.client.call(self.sdk.operation(operation), **kwargs)
        if response is None:
            raise EndpointError("the server returned no data")
        if is_error_response(response):
            raise EndpointError(error_message(response))
        return response

    async def _pages(
        self, operation: str, page_limit: int = PAGE_LIMIT, **kwargs: Any
    ) -> AsyncIterator[list[Any]]:
        """Each page of a collection in turn, following v8's pagination."""
        if not self.sdk.accepts(operation, "limit"):
            yield collection_items(await self._call(operation, **kwargs))
            return

        offset = 0
        seen = 0
        for _ in range(MAX_PAGES):
            response = await self._call(operation, limit=page_limit, offset=offset, **kwargs)
            page = collection_items(response)
            seen += len(page)
            yield page
            # The server may cap the page size below what was asked for, and says so
            limit = field(response, "limit") or page_limit
            if not page or len(page) < limit:
                return
            offset += len(page)

        _LOGGER.warning(
            "%s returned more than %d pages; showing the first %d items",
            operation,
            MAX_PAGES,
            seen,
        )

    async def _fetch_collection(
        self, operation: str, page_limit: int = PAGE_LIMIT, **kwargs: Any
    ) -> list[Any]:
        """Every item of a collection."""
        items: list[Any] = []
        async for page in self._pages(operation, page_limit=page_limit, **kwargs):
            items.extend(page)
        return items


class VeeamCoordinator(_VeeamCalls, DataUpdateCoordinator[dict[str, Any]]):
    """Fetches everything the entities show, once a minute."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Any,
        sdk: VeeamSdk,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.client = client
        self.sdk = sdk
        self._license_reason: str | None | bool = False  # False: not evaluated yet
        # Each job's latest session, kept between polls so that later polls only ask for
        # what is new, and when that last asking ran successfully
        self._job_sessions: dict[str, dict[str, Any]] = {}
        self._job_sessions_since: Any = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                try:
                    data = await self._fetch_all()
                except VeeamSessionError as err:
                    # The SDK dropped the session; the next call logs in again. Every call
                    # in a poll is a read, so trying once more is safe.
                    _LOGGER.debug("Session rejected (%s); retrying the poll once", err)
                    data = await self._fetch_all()
        except VeeamAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="authentication_failed",
                translation_placeholders={
                    "username": str(self.config_entry.data.get(CONF_USERNAME, ""))
                },
            ) from err
        except VeeamSessionError as err:
            raise UpdateFailed(
                f"The server rejected the session twice in a row: {describe_error(err)}"
            ) from err
        except TimeoutError as err:
            raise UpdateFailed(
                f"The server did not answer within {UPDATE_TIMEOUT:.0f} seconds"
            ) from err
        except TRANSPORT_ERRORS as err:
            raise UpdateFailed(f"Could not reach the server: {describe_error(err)}") from err
        except VeeamError as err:
            raise UpdateFailed(
                f"Error communicating with the server: {describe_error(err)}"
            ) from err

        self._update_license_issue(data)
        return data

    def _update_license_issue(self, data: dict[str, Any]) -> None:
        """Re-evaluate the unsupported-license repair when the answer changes."""
        if not fetch_succeeded(data, "license_info"):
            return
        reason = unsupported_license_reason(data.get("license_info"))
        if reason == self._license_reason:
            return
        self._license_reason = reason
        check_license_support(self.hass, self.config_entry, data)

    async def _fetch_all(self) -> dict[str, Any]:
        previous = self.data or {}
        data: dict[str, Any] = {}
        fetchers: dict[str, Callable[[], Awaitable[Any]]] = {
            "jobs": self._fetch_jobs,
            "copy_jobs": self._fetch_copy_jobs,
            "server_info": self._fetch_server_info,
            "license_info": self._fetch_license,
            "repositories": self._fetch_repositories,
            "proxies": self._fetch_proxies,
            "organizations": self._fetch_organizations,
        }
        if supports_organization_sync(self.sdk):
            # After the organizations, whose IDs v7 needs: this runs once data holds them
            # (freshly fetched, or the last known ones if that fetch failed)
            fetchers["organization_sync"] = lambda: self._fetch_organization_sync(
                data["organizations"]
            )
        if supports_repository_maintenance(self.sdk):
            fetchers["repository_maintenance"] = self._fetch_repository_maintenance
        if supports_job_sessions(self.sdk):
            fetchers["job_sessions"] = self._fetch_job_sessions
        if self.sdk.has_operation(HEALTH_OPERATION):
            fetchers["health"] = self._fetch_health

        fetch_ok: dict[str, bool] = {}
        errors: dict[str, str] = {}

        for key, fetcher in fetchers.items():
            try:
                data[key] = await fetcher()
                fetch_ok[key] = True
            except (VeeamAuthenticationError, VeeamSessionError):
                raise
            except EndpointError as err:
                errors[key] = str(err)
            except VeeamError as err:
                errors[key] = describe_error(err)
            except PARSE_ERRORS as err:
                errors[key] = f"could not parse the response: {describe_error(err)}"

            if key in errors:
                _LOGGER.warning("Failed to fetch %s: %s", key.replace("_", " "), errors[key])
                fetch_ok[key] = False
                # Keep what was last known, so a failed fetch does not look like an empty
                # collection. The entities go unavailable meanwhile (see entity.py).
                data[key] = previous.get(key, ENDPOINT_DEFAULTS[key])

        if not fetch_ok["jobs"] or not any(fetch_ok.values()):
            failed = "; ".join(f"{key}: {message}" for key, message in errors.items())
            raise UpdateFailed(f"Error fetching data from the server ({failed})")

        data["fetch_ok"] = fetch_ok
        data["diagnostics"] = {
            "connected": True,
            "health_ok": all(fetch_ok.values()),
            "failed_endpoints": sorted(key for key, ok in fetch_ok.items() if not ok),
            "last_successful_poll": dt_util.now(),
        }
        return data

    # -- calls ---------------------------------------------------------------

    async def _fetch_jobs(self) -> list[dict[str, Any]]:
        return parse_items("jobs", await self._fetch_collection("job.job_get"), parse_job)

    async def _fetch_copy_jobs(self) -> list[dict[str, Any]]:
        return parse_items(
            "copy jobs", await self._fetch_collection("copy_job.copy_job_get"), parse_copy_job
        )

    async def _fetch_repositories(self) -> list[dict[str, Any]]:
        items = await self._fetch_collection("backup_repository.backup_repository_get_repositories")
        repositories = parse_items("repositories", items, parse_repository)
        _LOGGER.debug("Fetched %d repositories", len(repositories))
        return repositories

    async def _fetch_proxies(self) -> list[dict[str, Any]]:
        return parse_items(
            "proxies", await self._fetch_collection("proxy.proxy_get_proxies"), parse_proxy
        )

    async def _fetch_organizations(self) -> list[dict[str, Any]]:
        items = await self._fetch_collection("organization.organization_get")
        organizations: list[dict[str, Any]] = []
        for org in items:
            # Licensing is a pair of counters; failing to read it should not cost the
            # organization, so it reads unknown instead
            licensing = None
            org_id = id_field(org)
            if org_id is not None and self.sdk.has_operation(LICENSING_OPERATION):
                try:
                    licensing = await self._call(LICENSING_OPERATION, organization_id=org_id)
                except (VeeamAuthenticationError, VeeamSessionError):
                    raise
                except (EndpointError, VeeamError, *PARSE_ERRORS) as err:
                    _LOGGER.debug(
                        "Could not fetch licensing for organization %s: %s",
                        org_id,
                        describe_error(err),
                    )
            organizations.extend(
                parse_items("organizations", [org], lambda o: parse_organization(o, licensing))
            )
        return organizations

    async def _fetch_organization_sync(
        self, organizations: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Sync state per organization ID."""
        if self.sdk.has_operation(SYNC_STATES_OPERATION):
            response = await self._call(SYNC_STATES_OPERATION)
            if not isinstance(response, list):
                raise EndpointError(f"expected a list, got {type(response).__name__}")
            states: dict[str, dict[str, Any]] = {}
            for state in response:
                org_id = id_field(state, "organization_id")
                if org_id is None:
                    continue
                try:
                    states[org_id] = parse_sync_state(state)
                except PARSE_ERRORS as err:
                    _LOGGER.warning(
                        "Skipping the sync state of organization %s: %s",
                        org_id,
                        describe_error(err),
                    )
            return states

        # v7: one call per organization. One that cannot be read is left out, so its sync
        # entities read unknown; the endpoint only fails when none can be read.
        states = {}
        errors: list[str] = []
        for org in organizations:
            try:
                state = await self._call(SYNC_STATE_OPERATION, organization_id=org["id"])
                states[org["id"]] = parse_sync_state(state)
            except (VeeamAuthenticationError, VeeamSessionError):
                raise
            except (EndpointError, VeeamError, *PARSE_ERRORS) as err:
                errors.append(describe_error(err))
                _LOGGER.debug(
                    "Could not fetch the sync state of organization %s: %s",
                    org["id"],
                    errors[-1],
                )
        if organizations and not states:
            raise EndpointError(errors[0])
        return states

    async def _fetch_job_sessions(self) -> dict[str, dict[str, Any]]:
        """Each job's and copy job's latest session, by job ID.

        Which sessions changed is found without paging through history on every poll:
        the running ones, plus those since the last poll (the first poll after startup looks
        back a day). endTimeLowerBound is documented as filtering on creation time; asking
        for the running sessions separately covers it filtering on the end time instead. A
        session last seen running that neither list returns has finished between two polls,
        so it is read by ID for its outcome.
        """
        started = dt_util.utcnow()
        since = self._job_sessions_since or started - timedelta(hours=JOB_SESSIONS_LOOKBACK_HOURS)
        since -= timedelta(minutes=JOB_SESSIONS_OVERLAP_MINUTES)
        running_filter = self.sdk.models.JobSessionGetStatus.RUNNING

        seen: dict[str, dict[str, Any]] = {}
        for kwargs in ({"status": running_filter}, {"end_time_lower_bound": since}):
            items = await self._fetch_collection(
                JOB_SESSIONS_OPERATION, page_limit=JOB_SESSIONS_PAGE_LIMIT, **kwargs
            )
            for session in parse_items(
                "job sessions", items, lambda item: parse_job_session(item, started)
            ):
                seen[session["session_id"]] = session

        for known in list(self._job_sessions.values()):
            if not known["is_running"] or known["session_id"] in seen:
                continue
            try:
                session = parse_job_session(
                    await self._call(JOB_SESSION_OPERATION, job_sessions_id=known["session_id"]),
                    started,
                )
            except (VeeamAuthenticationError, VeeamSessionError):
                raise
            except (EndpointError, VeeamError, *PARSE_ERRORS) as err:
                _LOGGER.debug(
                    "Could not re-read job session %s: %s", known["session_id"], describe_error(err)
                )
                continue
            if session is not None:
                seen[session["session_id"]] = session

        for session in sorted(seen.values(), key=lambda s: s["creation_time"] or started):
            if _newer(session, self._job_sessions.get(session["job_id"])):
                self._job_sessions[session["job_id"]] = session
        self._job_sessions_since = started
        return dict(self._job_sessions)

    async def _fetch_repository_maintenance(self) -> dict[str, dict[str, Any]]:
        sessions = parse_items(
            "maintenance sessions",
            await self._fetch_collection(MAINTENANCE_SESSIONS_OPERATION),
            parse_maintenance_session,
        )
        return maintenance_by_repository(sessions)

    async def _fetch_server_info(self) -> dict[str, Any]:
        return parse_server_info(await self._call("service_instance.service_instance_get"))

    async def _fetch_license(self) -> dict[str, Any]:
        license_data = await self._call("license_.license_get")

        # Auto-update is one binary sensor; its failure should not cost the whole license
        auto_update = None
        try:
            auto_update = await self._call("license_.license_get_auto_update")
        except (VeeamAuthenticationError, VeeamSessionError):
            raise
        except (EndpointError, VeeamError, *PARSE_ERRORS) as err:
            _LOGGER.debug("Could not fetch license auto-update: %s", describe_error(err))

        return parse_license(license_data, auto_update)

    async def _fetch_health(self) -> dict[str, Any]:
        response = await self.client.call(self.sdk.operation(HEALTH_OPERATION))
        if response is None:
            raise EndpointError("the server returned no data")
        if is_error_response(response):
            report = health_report_from_error(response, self.sdk.models)
            if report is None:
                raise EndpointError(error_message(response))
            response = report
        return parse_health(response)


class VeeamProtectedCountsCoordinator(_VeeamCalls, DataUpdateCoordinator[dict[str, Any]]):
    """Counts each organization's protected users, groups, sites and teams, hourly (v8).

    Its own coordinator, so that paging through a large tenant can neither slow the regular
    poll nor time it out. Each kind is counted on its own: one that fails keeps its last
    counts and reads unavailable, the others carry on. Items are counted page by page and
    not kept.

    Data: ``counts`` (organization ID -> kind -> number), ``fetch_ok`` (kind -> whether it
    was counted on the last run) and ``counted_at``. An organization with nothing of a kind
    is simply absent from that kind's counts: zero, once the kind was counted.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Any, sdk: VeeamSdk):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} protected objects",
            update_interval=timedelta(seconds=PROTECTED_COUNT_INTERVAL),
        )
        self.client = client
        self.sdk = sdk

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with asyncio.timeout(PROTECTED_COUNT_TIMEOUT):
                try:
                    return await self._count_all()
                except VeeamSessionError as err:
                    _LOGGER.debug("Session rejected (%s); counting once more", err)
                    return await self._count_all()
        except TimeoutError as err:
            raise UpdateFailed(
                f"Counting protected objects took longer than {PROTECTED_COUNT_TIMEOUT:.0f} "
                "seconds"
            ) from err
        # Refused credentials are the regular poll's to report (it starts reauth); here
        # they are one more reason the counts could not be refreshed
        except (VeeamError, *TRANSPORT_ERRORS) as err:
            raise UpdateFailed(f"Could not count protected objects: {describe_error(err)}") from err

    async def _count_all(self) -> dict[str, Any]:
        previous = self.data or {}
        previous_counts: dict[str, dict[str, int]] = previous.get("counts") or {}
        counts: dict[str, dict[str, int]] = {}
        fetch_ok: dict[str, bool] = {}

        for kind, operation in PROTECTED_OPERATIONS.items():
            try:
                per_organization = await self._count(operation)
            except (VeeamAuthenticationError, VeeamSessionError, *TRANSPORT_ERRORS):
                raise
            except (EndpointError, VeeamError, *PARSE_ERRORS) as err:
                _LOGGER.warning("Failed to count protected %s: %s", kind, describe_error(err))
                fetch_ok[kind] = False
                per_organization = {
                    org_id: org_counts[kind]
                    for org_id, org_counts in previous_counts.items()
                    if kind in org_counts
                }
            else:
                fetch_ok[kind] = True
            for org_id, number in per_organization.items():
                counts.setdefault(org_id, {})[kind] = number

        if not any(fetch_ok.values()):
            raise UpdateFailed("Could not count any kind of protected object")

        return {"counts": counts, "fetch_ok": fetch_ok, "counted_at": dt_util.now()}

    async def _count(self, operation: str) -> dict[str, int]:
        """How many items of one kind each organization has."""
        per_organization: dict[str, int] = {}
        async for page in self._pages(operation, page_limit=PROTECTED_PAGE_LIMIT):
            for item in page:
                org_id = id_field(item, "organization_id")
                if org_id is not None:
                    per_organization[org_id] = per_organization.get(org_id, 0) + 1
        return per_organization
