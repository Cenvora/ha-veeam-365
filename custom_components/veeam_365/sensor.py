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
        # coordinator.data is a dict with 'jobs' and 'license' keys
        if isinstance(coordinator.data, dict):
            # Create sensors for each backup job (each job gets its own device)
            jobs = coordinator.data.get("jobs", [])
            for job in jobs:
                entities.append(VeeamJobSensor(coordinator, entry, job))

            # Create license sensors if license data is available
            license_data = coordinator.data.get("license")
            if license_data:
                entities.extend(
                    [
                        VeeamLicenseSensor(
                            coordinator, entry, license_data, "license_id", "License ID"
                        ),
                        VeeamLicenseSensor(
                            coordinator, entry, license_data, "status", "License Status"
                        ),
                        VeeamLicenseSensor(
                            coordinator,
                            entry,
                            license_data,
                            "license_expires",
                            "License Expiration",
                            SensorDeviceClass.TIMESTAMP,
                        ),
                        VeeamLicenseSensor(
                            coordinator, entry, license_data, "type", "License Type"
                        ),
                        VeeamLicenseSensor(
                            coordinator, entry, license_data, "support_id", "Support ID"
                        ),
                        VeeamLicenseSensor(
                            coordinator, entry, license_data, "licensed_to", "Licensed To"
                        ),
                    ]
                )

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
        if self.coordinator.data and isinstance(self.coordinator.data, dict):
            jobs = self.coordinator.data.get("jobs", [])
            for job in jobs:
                if job.get("id") == self._job_id:
                    return job.get("status", "unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if self.coordinator.data and isinstance(self.coordinator.data, dict):
            jobs = self.coordinator.data.get("jobs", [])
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


class VeeamLicenseSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam License sensor."""

    def __init__(
        self,
        coordinator,
        config_entry,
        license_data,
        attribute_name,
        friendly_name,
        device_class=None,
    ):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attribute_name = attribute_name
        self._attr_unique_id = f"{config_entry.entry_id}_license_{attribute_name}"
        self._attr_name = friendly_name
        self._attr_has_entity_name = True
        if device_class:
            self._attr_device_class = device_class

        # Set appropriate icon based on attribute
        if attribute_name == "status":
            self._attr_icon = "mdi:shield-check"
        elif attribute_name == "license_expires":
            self._attr_icon = "mdi:calendar-clock"
        elif attribute_name == "type":
            self._attr_icon = "mdi:license"
        else:
            self._attr_icon = "mdi:information"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if self.coordinator.data and isinstance(self.coordinator.data, dict):
            license_data = self.coordinator.data.get("license")
            if license_data:
                value = license_data.get(self._attribute_name)
                # Convert datetime to ISO format string for display
                if hasattr(value, "isoformat"):
                    return value.isoformat()
                return str(value) if value is not None else None
        return None

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_license")},
            "name": "Veeam License",
            "manufacturer": "Veeam",
            "model": "License Information",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }
