"""The Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


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
        api_version="v8",
    )

    # Create update coordinator
    async def async_update_data():
        """Fetch data from API."""
        try:
            # Ensure client is connected (VeeamClient handles idempotency)
            await veeam_client.connect()

            # Get backup jobs using the veeam-365 library
            # Note: Pass method reference (not call) to veeam_client.call()
            jobs_response = await veeam_client.call(
                veeam_client.api("backup_job").backup_job_get_jobs
            )

            # Process the response
            if not jobs_response or not hasattr(jobs_response, "data"):
                return []

            # Convert jobs to a list of dictionaries for easier processing
            jobs = []
            for job in jobs_response.data:
                jobs.append(
                    {
                        "id": getattr(job, "id", None),
                        "name": getattr(job, "name", "Unknown"),
                        "status": getattr(job, "status", "unknown"),
                        "type": getattr(job, "type", None),
                        "last_run": getattr(job, "last_run", None),
                        "next_run": getattr(job, "next_run", None),
                        "last_result": getattr(job, "last_result", None),
                    }
                )

            return jobs

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

    # Store coordinator and veeam client
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "veeam_client": veeam_client,
    }

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Close the Veeam client
    veeam_client = hass.data[DOMAIN][entry.entry_id].get("veeam_client")
    if veeam_client:
        await veeam_client.close()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
