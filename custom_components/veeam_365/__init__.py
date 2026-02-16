"""The Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

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
    api_module = API_VERSIONS.get(api_version, "v8")

    # Import UNSET type for proper type checking
    try:
        types_module = importlib.import_module(f"veeam_365.{api_module}.types")
        UNSET = types_module.UNSET
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_365 types: %s", err)
        return False

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    # Create VeeamClient directly - it handles token rotation automatically
    veeam_client = VeeamClient(
        host=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        api_version=api_version,
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    # Connect to Veeam API
    try:
        await veeam_client.connect()
    except Exception as err:
        _LOGGER.error("Failed to connect to Veeam API: %s", err)
        return False

    async def async_update_data():
        """Fetch data from API."""
        # Track connection state for diagnostic sensors
        connected = False
        health_ok = False
        last_successful_poll = None

        try:
            # VeeamClient handles token refresh automatically in call() method
            # No need for manual token validation

            # Mark as connected
            connected = True

            # Fetch jobs data
            jobs_response = await veeam_client.call(veeam_client.api("job").get_jobs)

            if not jobs_response:
                raise UpdateFailed("Jobs API returned no data")

            # Access the .results field from JobsResult
            jobs_data = jobs_response.results if jobs_response else []

            # Helper function to safely get enum value
            def get_enum_value(enum_val, default="unknown"):
                """Extract enum value, handling both enum types and UNSET."""
                if enum_val is None or enum_val is UNSET:
                    return default
                # Try to get enum value
                if hasattr(enum_val, "value"):
                    return enum_val.value
                return str(enum_val)

            # Helper function to safely get datetime
            def get_datetime_value(dt_val):
                """Extract datetime value, handling UNSET."""
                if dt_val is None or dt_val is UNSET:
                    return None
                return dt_val

            # Helper to safely get UUID as string
            def get_uuid_value(uuid_val):
                """Extract UUID value."""
                if uuid_val is None or uuid_val is UNSET:
                    return None
                return str(uuid_val)

            jobs_list = []
            for job in jobs_data:
                try:
                    job_dict = {
                        "id": get_uuid_value(job.id),
                        "name": job.name or "Unknown",
                        "description": getattr(job, "description", "") or "",
                        "is_enabled": getattr(job, "is_enabled", True),
                        "last_run": get_datetime_value(getattr(job, "last_run", None)),
                        "next_run": get_datetime_value(getattr(job, "next_run", None)),
                        "last_status": get_enum_value(getattr(job, "last_status", None)),
                    }
                    jobs_list.append(job_dict)
                except (AttributeError, TypeError) as err:
                    _LOGGER.warning("Failed to parse job: %s", err)
                    continue

            # Fetch organizations data
            organizations_list = []
            try:
                orgs_response = await veeam_client.call(
                    veeam_client.api("organization").get_organizations
                )
                if orgs_response:
                    orgs_data = orgs_response.results if orgs_response else []
                    for org in orgs_data:
                        try:
                            org_dict = {
                                "id": get_uuid_value(org.id),
                                "name": org.name or "Unknown",
                                "region": getattr(org, "region", "Unknown"),
                                "is_backup_enabled": getattr(org, "is_backup_enabled", False),
                            }
                            organizations_list.append(org_dict)
                        except (AttributeError, TypeError) as err:
                            _LOGGER.warning("Failed to parse organization: %s", err)
                            continue
            except Exception as err:
                _LOGGER.warning("Failed to fetch organizations: %s", err)

            # Fetch repositories data
            repositories_list = []
            try:
                repos_response = await veeam_client.call(
                    veeam_client.api("backup_repository").get_repositories
                )
                if repos_response:
                    repos_data = repos_response.results if repos_response else []
                    for repo in repos_data:
                        try:
                            repo_dict = {
                                "id": get_uuid_value(repo.id),
                                "name": repo.name or "Unknown",
                                "description": getattr(repo, "description", "") or "",
                                "type": get_enum_value(getattr(repo, "repository_type", None)),
                            }
                            repositories_list.append(repo_dict)
                        except (AttributeError, TypeError) as err:
                            _LOGGER.warning("Failed to parse repository: %s", err)
                            continue
            except Exception as err:
                _LOGGER.warning("Failed to fetch repositories: %s", err)

            # Fetch license information
            license_info = None
            try:
                license_data = await veeam_client.call(
                    veeam_client.api("license_").get_license_information
                )
                if license_data:
                    license_info = {
                        "status": get_enum_value(getattr(license_data, "status", None)),
                        "expiration_date": get_datetime_value(
                            getattr(license_data, "expiration_date", None)
                        ),
                        "licensed_users": getattr(license_data, "licensed_users", 0),
                        "used_users": getattr(license_data, "used_users", 0),
                    }
            except Exception as err:
                _LOGGER.warning("Failed to fetch license info: %s", err)

            # Update diagnostic values - successful poll
            health_ok = True
            last_successful_poll = dt_util.now()

            return {
                "jobs": jobs_list,
                "organizations": organizations_list,
                "repositories": repositories_list,
                "license_info": license_info,
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
