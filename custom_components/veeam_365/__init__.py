"""The Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class VeeamRuntimeData:
    """Runtime data for Veeam integration."""

    coordinator: DataUpdateCoordinator
    veeam_client: any


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup for Microsoft 365 from a config entry."""
    # Import the veeam_365 library
    try:
        from veeam_365.client import VeeamClient
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_365 library: %s", err)
        return False

    # Construct base URL
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    # Create VeeamClient for API interactions
    veeam_client = VeeamClient(
        host=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        api_version=entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
    )

    # Create update coordinator
    async def async_update_data():
        """Fetch data from API."""
        try:
            # Ensure client is connected (VeeamClient handles idempotency)
            # Run connect in executor to avoid blocking import_module calls
            def _connect_sync():
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(veeam_client.connect())
                finally:
                    loop.close()

            await hass.async_add_executor_job(_connect_sync)

            # Fetch all data: jobs, license, and server info
            data = {
                "jobs": [],
                "license": None,
                "server": None,
            }

            # Get backup jobs
            try:
                _LOGGER.debug("Fetching backup jobs from API")
                jobs_response = await veeam_client.call(veeam_client.api("job").job_get)
                _LOGGER.debug("Jobs response received: %s", type(jobs_response))
                if jobs_response and hasattr(jobs_response, "results"):
                    for job in jobs_response.results:
                        # Convert last_status enum to lowercase string
                        status = "unknown"
                        if hasattr(job, "last_status") and job.last_status:
                            status = str(job.last_status).lower()

                        data["jobs"].append(
                            {
                                "id": str(job.id) if job.id else None,
                                "name": job.name if hasattr(job, "name") else "Unknown",
                                "status": status,
                                "backup_type": (
                                    str(job.backup_type)
                                    if hasattr(job, "backup_type") and job.backup_type
                                    else None
                                ),
                                "last_run": job.last_run if hasattr(job, "last_run") else None,
                                "next_run": job.next_run if hasattr(job, "next_run") else None,
                                "is_enabled": (
                                    job.is_enabled if hasattr(job, "is_enabled") else None
                                ),
                                "total_objects": (
                                    job.total_objects if hasattr(job, "total_objects") else None
                                ),
                                "processed_objects": (
                                    job.processed_objects
                                    if hasattr(job, "processed_objects")
                                    else None
                                ),
                            }
                        )
            except Exception as err:
                _LOGGER.error(
                    "Failed to fetch jobs: %s - %s", type(err).__name__, err, exc_info=True
                )

            # Get license information
            try:
                _LOGGER.debug("Fetching license information from API")
                license_response = await veeam_client.call(veeam_client.api("license").license_get)
                _LOGGER.debug("License response received: %s", type(license_response))
                if license_response:
                    data["license"] = {
                        "license_id": (
                            str(license_response.license_id)
                            if hasattr(license_response, "license_id")
                            else None
                        ),
                        "status": (
                            str(license_response.status)
                            if hasattr(license_response, "status")
                            else None
                        ),
                        "type": (
                            str(license_response.type)
                            if hasattr(license_response, "type")
                            else None
                        ),
                        "expiration_date": (
                            license_response.license_expires
                            if hasattr(license_response, "license_expires")
                            else None
                        ),
                        "licensed_to": (
                            license_response.licensed_to
                            if hasattr(license_response, "licensed_to")
                            else None
                        ),
                        "total_users": (
                            license_response.total_number
                            if hasattr(license_response, "total_number")
                            else None
                        ),
                        "used_users": (
                            license_response.used_number
                            if hasattr(license_response, "used_number")
                            else None
                        ),
                        "new_users": (
                            license_response.new_number
                            if hasattr(license_response, "new_number")
                            else None
                        ),
                    }
            except Exception as err:
                _LOGGER.error(
                    "Failed to fetch license: %s - %s", type(err).__name__, err, exc_info=True
                )

            # Get server information
            try:
                _LOGGER.debug("Fetching server information from API")
                server_response = await veeam_client.call(
                    veeam_client.api("service").service_get_version
                )
                _LOGGER.debug("Server response received: %s", type(server_response))
                if server_response:
                    data["server"] = {
                        "version": (
                            str(server_response.version)
                            if hasattr(server_response, "version")
                            else None
                        ),
                        "build": (
                            str(server_response.build)
                            if hasattr(server_response, "build")
                            else None
                        ),
                    }
            except Exception as err:
                _LOGGER.error(
                    "Failed to fetch server info: %s - %s", type(err).__name__, err, exc_info=True
                )

            return data

        except PermissionError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store runtime data using runtime_data (Bronze tier requirement)
    entry.runtime_data = VeeamRuntimeData(
        coordinator=coordinator,
        veeam_client=veeam_client,
    )

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Close the Veeam client
    if hasattr(entry, "runtime_data") and entry.runtime_data:
        await entry.runtime_data.veeam_client.close()

    # Unload platforms
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
