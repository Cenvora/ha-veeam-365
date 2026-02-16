"""Support for Veeam Backup for Microsoft 365 sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Silver tier requirement: Specify parallel updates
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam Backup for Microsoft 365 sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator

    # Create sensors
    entities = []

    if coordinator.data:
        # Create sensors for each backup job (each job gets its own device)
        # coordinator.data is now a flat list of jobs
        jobs = coordinator.data if isinstance(coordinator.data, list) else []
        for job in jobs:
            entities.append(VeeamJobSensor(coordinator, entry, job))

    async_add_entities(entities)


class VeeamJobSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam Backup Job sensor."""

    def __init__(self, coordinator, config_entry, job_data):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}"
        self._attr_name = f"{self._job_name} Status"
        self._attr_icon = "mdi:backup-restore"
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if self.coordinator.data:
            # coordinator.data is a flat list of jobs
            jobs = self.coordinator.data if isinstance(self.coordinator.data, list) else []
            for job in jobs:
                if job.get("id") == self._job_id:
                    return job.get("status", "unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if self.coordinator.data:
            jobs = self.coordinator.data if isinstance(self.coordinator.data, list) else []
            for job in jobs:
                if job.get("id") == self._job_id:
                    return {
                        "job_id": job.get("id"),
                        "job_name": job.get("name"),
                        "backup_type": job.get("backup_type"),
                        "last_run": job.get("last_run"),
                        "next_run": job.get("next_run"),
                        "is_enabled": job.get("is_enabled"),
                        "total_objects": job.get("total_objects"),
                        "processed_objects": job.get("processed_objects"),
                    }
        return {}

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_job_{self._job_id}")},
            "name": self._job_name,
            "manufacturer": "Veeam",
            "model": "Backup Job",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }
