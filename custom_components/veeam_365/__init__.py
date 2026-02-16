"""The Veeam Backup & Replication integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL, DOMAIN, UPDATE_INTERVAL
from .token_manager import VeeamTokenManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup & Replication from a config entry."""
    # Import the veeam_br library
    try:
        from veeam_br.v1_3_rev1.api.jobs import get_all_jobs
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_br library: %s", err)
        return False

    # Construct base URL
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    # Create token manager for handling token refresh
    token_manager = VeeamTokenManager(
        base_url=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    # Create update coordinator
    async def async_update_data():
        """Fetch data from API."""
        try:
            # Ensure we have a valid token before making API calls
            if not await token_manager.ensure_valid_token(hass):
                raise UpdateFailed("Failed to obtain valid access token")

            # Get authenticated client
            client = token_manager.get_authenticated_client()
            if not client:
                raise UpdateFailed("No authenticated client available")

            # Get backup jobs and their status using the veeam-br library
            def _get_jobs():
                return get_all_jobs.sync(client=client, x_api_version="1.3-rev1")

            jobs_response = await hass.async_add_executor_job(_get_jobs)

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

    # Store coordinator and token manager
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "token_manager": token_manager,
    }

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
