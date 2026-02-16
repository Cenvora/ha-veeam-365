"""The Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

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
            await veeam_client.connect()

            # Get backup jobs using the veeam-365 library
            # Note: Pass method reference (not call) to veeam_client.call()
            jobs_response = await veeam_client.call(veeam_client.api("job").job_get)

            # Process the response
            if not jobs_response or not hasattr(jobs_response, "results"):
                return []

            # Convert jobs to a list of dictionaries for easier processing
            jobs = []
            for job in jobs_response.results:
                # Convert last_status enum to lowercase string
                status = "unknown"
                if hasattr(job, "last_status") and job.last_status:
                    status = str(job.last_status).lower()

                jobs.append(
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
                        "is_enabled": job.is_enabled if hasattr(job, "is_enabled") else None,
                        "total_objects": (
                            job.total_objects if hasattr(job, "total_objects") else None
                        ),
                        "processed_objects": (
                            job.processed_objects if hasattr(job, "processed_objects") else None
                        ),
                    }
                )

            return jobs

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
