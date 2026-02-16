"""The Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import importlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup for Microsoft 365 from a config entry."""
    from veeam_365.client import VeeamClient

    api_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    # Convert display version (e.g., "8") to module version (e.g., "v8") for VeeamClient
    api_module = API_VERSIONS.get(api_version, "v8")

    # Import UNSET type for proper type checking
    try:
        types_module = await asyncio.to_thread(
            importlib.import_module, f"veeam_365.{api_module}.types"
        )
        UNSET = types_module.UNSET
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_365 types: %s", err)
        return False

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    # Create and connect VeeamClient
    try:
        import json

        # VeeamClient constructor is not blocking, can create directly
        veeam_client = VeeamClient(
            host=base_url,
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            api_version=api_module,
            verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            disable_antiforgery_token=True,
        )

        _LOGGER.debug(
            "Connecting to Veeam server at %s (api_version=%s, verify_ssl=%s)",
            base_url,
            api_module,
            entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
        try:
            await veeam_client.connect()
            _LOGGER.info("Successfully connected to Veeam server at %s", base_url)
        except json.JSONDecodeError as err:
            # Server returned non-JSON response (likely an error with empty body)
            _LOGGER.error(
                "Server at %s returned invalid JSON (empty or non-JSON response). "
                "Exception: %s. Position: line %s column %s. "
                "Common causes: 401 Unauthorized, 403 Forbidden, 500 Server Error. "
                "Check credentials, API permissions, and server accessibility.",
                base_url,
                err.msg,
                err.lineno,
                err.colno,
            )
            raise ConnectionError(
                f"Server returned invalid response. Check credentials and server status."
            ) from err
        except Exception as err:
            _LOGGER.error(
                "Unexpected error during connection to %s: %s (type: %s)",
                base_url,
                err,
                type(err).__name__,
                exc_info=True,
            )
            raise
    except Exception as err:
        _LOGGER.error(
            "Failed to setup Veeam integration at %s: %s",
            base_url,
            err,
        )
        return False

    async def async_update_data():
        """Fetch data from API."""

        # Helper function to check if API response is an error
        def is_api_error(response):
            """Check if response is a RESTExceptionInfo or other error type."""
            if response is None:
                return True
            # Check if it's a RESTExceptionInfo object (has 'message' attribute indicating error)
            if hasattr(response, "message") and hasattr(response, "error_code"):
                return True
            # Check for class name containing 'ExceptionInfo'
            if hasattr(response, "__class__") and "ExceptionInfo" in response.__class__.__name__:
                return True
            return False

        def get_error_message(response):
            """Extract error message from API error response."""
            if hasattr(response, "message"):
                return str(response.message)
            return f"API error: {response}"

        # Track connection state for diagnostic sensors
        connected = False
        health_ok = False
        last_successful_poll = None

        try:
            # VeeamClient handles token refresh automatically in call() method
            # No need for manual token validation

            # Mark as connected
            connected = True

            # Fetch jobs data from Jobs endpoint
            jobs_list = []
            try:
                jobs_api = await asyncio.to_thread(veeam_client.api, "job")
                jobs_response = await veeam_client.call(jobs_api.job_get)

                # Check for API errors
                if is_api_error(jobs_response):
                    _LOGGER.error("Failed to fetch jobs: %s", get_error_message(jobs_response))
                    jobs_response = None

                if jobs_response and hasattr(jobs_response, "results"):
                    for job in jobs_response.results:
                        # Helper to get enum value
                        def get_job_enum_attr(obj, attr_name, default="Unknown"):
                            attr = getattr(obj, attr_name, None)
                            if attr is None:
                                return default
                            if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                                return default
                            if hasattr(attr, "value"):
                                return attr.value
                            return str(attr)

                        # Helper to get datetime
                        def get_job_datetime_attr(obj, attr_name):
                            attr = getattr(obj, attr_name, None)
                            if attr is None:
                                return None
                            if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                                return None
                            return attr

                        job_data = {
                            "id": getattr(job, "id", None),
                            "name": getattr(job, "name", "Unknown Job"),
                            "backup_type": get_job_enum_attr(job, "backup_type"),
                            "last_run": get_job_datetime_attr(job, "last_run"),
                            "next_run": get_job_datetime_attr(job, "next_run"),
                            "last_backup": get_job_datetime_attr(job, "last_backup"),
                            "is_enabled": getattr(job, "is_enabled", False),
                            "last_status": get_job_enum_attr(job, "last_status"),
                        }
                        jobs_list.append(job_data)
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse jobs data: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch jobs data: %s", err)

            # Fetch copy jobs data from CopyJobs endpoint
            copy_jobs_list = []
            try:
                copy_jobs_api = await asyncio.to_thread(veeam_client.api, "copy_job")
                copy_jobs_response = await veeam_client.call(copy_jobs_api.copy_job_get)

                # Check for API errors
                if is_api_error(copy_jobs_response):
                    _LOGGER.error(
                        "Failed to fetch copy jobs: %s", get_error_message(copy_jobs_response)
                    )
                    copy_jobs_response = None

                if copy_jobs_response and hasattr(copy_jobs_response, "results"):
                    for copy_job in copy_jobs_response.results:
                        # Helper to get enum value
                        def get_copy_job_enum_attr(obj, attr_name, default="Unknown"):
                            attr = getattr(obj, attr_name, None)
                            if attr is None:
                                return default
                            if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                                return default
                            if hasattr(attr, "value"):
                                return attr.value
                            return str(attr)

                        # Helper to get datetime
                        def get_copy_job_datetime_attr(obj, attr_name):
                            attr = getattr(obj, attr_name, None)
                            if attr is None:
                                return None
                            if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                                return None
                            return attr

                        copy_job_data = {
                            "id": getattr(copy_job, "id", None),
                            "name": getattr(copy_job, "name", "Unknown Copy Job"),
                            "backup_job_id": getattr(copy_job, "backup_job_id", None),
                            "last_run": get_copy_job_datetime_attr(copy_job, "last_run"),
                            "last_backup": get_copy_job_datetime_attr(copy_job, "last_backup"),
                            "is_enabled": getattr(copy_job, "is_enabled", False),
                            "last_status": get_copy_job_enum_attr(copy_job, "last_status"),
                        }
                        copy_jobs_list.append(copy_job_data)
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse copy jobs data: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch copy jobs data: %s", err)

            # Fetch server information from ServiceInstance endpoint
            server_info = None
            try:
                service_instance_api = await asyncio.to_thread(veeam_client.api, "service_instance")
                service_instance_data = await veeam_client.call(
                    service_instance_api.service_instance_get
                )

                # Check for API errors
                if is_api_error(service_instance_data):
                    _LOGGER.error(
                        "Failed to fetch service instance info: %s",
                        get_error_message(service_instance_data),
                    )
                    service_instance_data = None

                if service_instance_data:
                    server_info = {
                        "installation_id": getattr(
                            service_instance_data, "installation_id", "Unknown"
                        ),
                        "version": getattr(service_instance_data, "version", "Unknown"),
                    }
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse service instance info: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch service instance info: %s", err)

            # Fetch license information
            license_info = None
            try:
                license_api = await asyncio.to_thread(veeam_client.api, "license_")
                license_data = await veeam_client.call(license_api.license_get)

                # Check for API errors
                if is_api_error(license_data):
                    _LOGGER.error(
                        "Failed to fetch license info: %s", get_error_message(license_data)
                    )
                    license_data = None

                license_auto_update_data = await veeam_client.call(
                    license_api.license_get_auto_update
                )

                # Check for API errors
                if is_api_error(license_auto_update_data):
                    _LOGGER.error(
                        "Failed to fetch license auto-update info: %s",
                        get_error_message(license_auto_update_data),
                    )
                    license_auto_update_data = None

                if license_data:
                    # Helper function to safely get enum value from object attribute
                    def get_license_enum_attr(obj, attr_name, default="Unknown"):
                        """Extract enum value from object attribute, handling both enum types and UNSET."""
                        attr = getattr(obj, attr_name, None)
                        if attr is None:
                            return default
                        # Check if it's UNSET (from veeam-br library)
                        if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                            return default
                        # Try to get enum value
                        if hasattr(attr, "value"):
                            return attr.value
                        return str(attr)

                    # Helper function to safely get datetime from object attribute
                    def get_license_datetime_attr(obj, attr_name):
                        """Extract datetime value from object attribute, handling UNSET."""
                        attr = getattr(obj, attr_name, None)
                        if attr is None:
                            return None
                        # Check if it's UNSET
                        if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                            return None
                        return attr

                    license_info = {
                        "status": get_license_enum_attr(license_data, "status"),
                        "type": get_license_enum_attr(license_data, "type_"),
                        "expiration_date": get_license_datetime_attr(
                            license_data, "license_expires"
                        ),
                        "grace_period_expires": get_license_datetime_attr(
                            license_data, "grace_period_expires"
                        ),
                        "licensed_to": getattr(license_data, "licensed_to", "Unknown"),
                        "email": getattr(license_data, "email", "Unknown"),
                        "package": getattr(license_data, "package", "Unknown"),
                        "total_number": getattr(license_data, "total_number", 0),
                        "used_number": getattr(license_data, "used_number", 0),
                        "new_number": getattr(license_data, "new_number", 0),
                        "auto_update_enabled": (
                            getattr(license_auto_update_data, "is_enabled", False)
                            if license_auto_update_data
                            else False
                        ),
                    }
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse license info: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch license info: %s", err)

            # Fetch repositories information from /BackupRepositories
            repositories_list = []
            try:
                repositories_api = getattr(veeam_client, "backup_repository", None)
                if repositories_api and hasattr(
                    repositories_api, "backup_repository_get_repositories"
                ):
                    _LOGGER.debug("Fetching repositories from /BackupRepositories")
                    repos_response = await hass.async_add_executor_job(
                        repositories_api.backup_repository_get_repositories
                    )

                    if repos_response and hasattr(repos_response, "results"):
                        for repo_data in repos_response.results:
                            # Get object storage details
                            object_storage = getattr(repo_data, "object_storage", None)
                            is_immutable = False
                            immutability_days = None
                            storage_type = "Unknown"
                            used_space_gb = None

                            if object_storage:
                                # Check for Unset on enable_immutability
                                enable_immutability_attr = getattr(
                                    object_storage, "enable_immutability", False
                                )
                                if (
                                    hasattr(enable_immutability_attr, "__class__")
                                    and enable_immutability_attr.__class__.__name__ == "Unset"
                                ):
                                    is_immutable = False
                                else:
                                    is_immutable = bool(enable_immutability_attr)

                                # Get immutability days
                                immutability_days_attr = getattr(
                                    object_storage, "immutability_period_days", None
                                )
                                if immutability_days_attr is not None and not (
                                    hasattr(immutability_days_attr, "__class__")
                                    and immutability_days_attr.__class__.__name__ == "Unset"
                                ):
                                    immutability_days = immutability_days_attr

                                # Get storage type from enum
                                type_attr = getattr(object_storage, "type_", None)
                                if type_attr and hasattr(type_attr, "value"):
                                    storage_type = type_attr.value
                                elif type_attr:
                                    storage_type = str(type_attr)

                                # Get used space from object storage (in bytes)
                                used_bytes_attr = getattr(object_storage, "used_space_bytes", None)
                                if used_bytes_attr is not None and not (
                                    hasattr(used_bytes_attr, "__class__")
                                    and used_bytes_attr.__class__.__name__ == "Unset"
                                ):
                                    used_space_gb = round(used_bytes_attr / (1024**3), 2)

                            # Get retention type from enum
                            retention_type_attr = getattr(repo_data, "retention_type", None)
                            retention_type = "Unknown"
                            if retention_type_attr and hasattr(retention_type_attr, "value"):
                                retention_type = retention_type_attr.value
                            elif retention_type_attr:
                                retention_type = str(retention_type_attr)

                            repositories_list.append(
                                {
                                    "id": str(getattr(repo_data, "id", "")),
                                    "name": getattr(repo_data, "name", "Unknown Repository"),
                                    "description": getattr(repo_data, "description", ""),
                                    "path": getattr(repo_data, "path", ""),
                                    "type": storage_type,
                                    "retention_type": retention_type,
                                    "used_space_gb": used_space_gb,
                                    "is_long_term": getattr(repo_data, "is_long_term", False)
                                    or False,
                                    "is_outdated": getattr(repo_data, "is_outdated", False)
                                    or False,
                                    "is_out_of_sync": getattr(repo_data, "is_out_of_sync", False)
                                    or False,
                                    "is_indexed": getattr(repo_data, "is_indexed", False) or False,
                                    "is_out_of_order": getattr(repo_data, "is_out_of_order", False)
                                    or False,
                                    "is_immutable": is_immutable,
                                    "immutability_days": immutability_days,
                                    # Derived status fields for binary sensors
                                    "is_online": not getattr(repo_data, "is_out_of_sync", False),
                                    "is_out_of_date": getattr(repo_data, "is_outdated", False)
                                    or False,
                                    "is_accessible": not getattr(
                                        repo_data, "is_out_of_order", False
                                    ),
                                }
                            )
                        _LOGGER.debug("Fetched %d repositories", len(repositories_list))
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse repositories info: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch repositories info: %s", err)

            # Update diagnostic values - successful poll
            health_ok = True
            last_successful_poll = dt_util.now()

            return {
                "jobs": jobs_list,
                "copy_jobs": copy_jobs_list,
                "server_info": server_info,
                "license_info": license_info,
                "repositories": repositories_list,
                "diagnostics": {
                    "connected": connected,
                    "health_ok": health_ok,
                    "last_successful_poll": last_successful_poll,
                },
            }

        except Exception as err:
            # When an update fails, the coordinator retains the last successful data,
            # so diagnostic sensors will continue to show the last successful poll time
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = {
        "coordinator": coordinator,
        "veeam_client": veeam_client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
