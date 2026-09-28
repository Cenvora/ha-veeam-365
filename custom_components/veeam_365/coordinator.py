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
from collections.abc import Awaitable, Callable
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

from .const import DOMAIN, MAX_PAGES, PAGE_LIMIT, UPDATE_INTERVAL, UPDATE_TIMEOUT
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
    "health": None,
}

# The server's own health report (NATS and the configuration database). API v8 only; on
# older versions it is not fetched at all rather than reported as a failing endpoint.
HEALTH_OPERATION = "health.health_get"

# Collections whose items become devices, keyed by data key
COLLECTIONS = ("jobs", "copy_jobs", "repositories", "proxies")

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


class VeeamCoordinator(DataUpdateCoordinator[dict[str, Any]]):
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
        fetchers: dict[str, Callable[[], Awaitable[Any]]] = {
            "jobs": self._fetch_jobs,
            "copy_jobs": self._fetch_copy_jobs,
            "server_info": self._fetch_server_info,
            "license_info": self._fetch_license,
            "repositories": self._fetch_repositories,
            "proxies": self._fetch_proxies,
        }
        if self.sdk.has_operation(HEALTH_OPERATION):
            fetchers["health"] = self._fetch_health

        data: dict[str, Any] = {}
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

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        """Call one operation; raise EndpointError when it answers with an error."""
        response = await self.client.call(self.sdk.operation(operation), **kwargs)
        if response is None:
            raise EndpointError("the server returned no data")
        if is_error_response(response):
            raise EndpointError(error_message(response))
        return response

    async def _fetch_collection(self, operation: str) -> list[Any]:
        """Fetch every item of a collection, following v8's pagination."""
        if not self.sdk.accepts(operation, "limit"):
            return collection_items(await self._call(operation))

        items: list[Any] = []
        offset = 0
        for _ in range(MAX_PAGES):
            response = await self._call(operation, limit=PAGE_LIMIT, offset=offset)
            page = collection_items(response)
            items.extend(page)
            # The server may cap the page size below what was asked for, and says so
            limit = field(response, "limit") or PAGE_LIMIT
            if not page or len(page) < limit:
                return items
            offset += len(page)

        _LOGGER.warning(
            "%s returned more than %d pages; showing the first %d items",
            operation,
            MAX_PAGES,
            len(items),
        )
        return items

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
