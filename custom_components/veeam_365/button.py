"""Button platform for Veeam Backup for Microsoft 365."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam 365 button based on a config entry."""
    coordinator = entry.runtime_data["coordinator"]
    veeam_client = entry.runtime_data["veeam_client"]

    entities = []

    # Add job control buttons
    if coordinator.data and "jobs" in coordinator.data:
        for job in coordinator.data["jobs"]:
            entities.extend(
                [
                    Veeam365JobStartButton(coordinator, veeam_client, job),
                    Veeam365JobStopButton(coordinator, veeam_client, job),
                    Veeam365JobRetryButton(coordinator, veeam_client, job),
                ]
            )

    async_add_entities(entities)


class Veeam365ButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for Veeam 365 buttons."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, veeam_client, device_id: str, device_name: str, device_model: str
    ):
        """Initialize the button."""
        super().__init__(coordinator)
        self._veeam_client = veeam_client
        self._device_id = device_id
        self._device_name = device_name
        self._device_model = device_model

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device_name,
            "manufacturer": "Veeam",
            "model": self._device_model,
        }


class Veeam365JobStartButton(Veeam365ButtonBase):
    """Button to start a job."""

    def __init__(self, coordinator, veeam_client, job):
        """Initialize the button."""
        super().__init__(coordinator, veeam_client, f"job_{job['id']}", job["name"], "Backup Job")
        self._job_id = job["id"]
        self._attr_unique_id = f"{DOMAIN}_{job['id']}_start"
        self._attr_translation_key = "job_start"
        self._attr_icon = "mdi:play"

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self._veeam_client.call(
                self._veeam_client.api("job").start_job,
                job_id=self._job_id,
            )
            _LOGGER.info("Started job %s", self._job_id)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to start job %s: %s", self._job_id, err)
            raise


class Veeam365JobStopButton(Veeam365ButtonBase):
    """Button to stop a job."""

    def __init__(self, coordinator, veeam_client, job):
        """Initialize the button."""
        super().__init__(coordinator, veeam_client, f"job_{job['id']}", job["name"], "Backup Job")
        self._job_id = job["id"]
        self._attr_unique_id = f"{DOMAIN}_{job['id']}_stop"
        self._attr_translation_key = "job_stop"
        self._attr_icon = "mdi:stop"

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self._veeam_client.call(
                self._veeam_client.api("job").stop_job,
                job_id=self._job_id,
            )
            _LOGGER.info("Stopped job %s", self._job_id)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to stop job %s: %s", self._job_id, err)
            raise


class Veeam365JobRetryButton(Veeam365ButtonBase):
    """Button to retry a job."""

    def __init__(self, coordinator, veeam_client, job):
        """Initialize the button."""
        super().__init__(coordinator, veeam_client, f"job_{job['id']}", job["name"], "Backup Job")
        self._job_id = job["id"]
        self._attr_unique_id = f"{DOMAIN}_{job['id']}_retry"
        self._attr_translation_key = "job_retry"
        self._attr_icon = "mdi:refresh"

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self._veeam_client.call(
                self._veeam_client.api("job").retry_job,
                job_id=self._job_id,
            )
            _LOGGER.info("Retried job %s", self._job_id)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to retry job %s: %s", self._job_id, err)
            raise
