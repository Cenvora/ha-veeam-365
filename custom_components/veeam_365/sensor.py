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
        # Create license sensors
        if coordinator.data.get("license"):
            entities.extend(
                [
                    VeeamLicenseStatusSensor(coordinator, entry),
                    VeeamLicenseTypeSensor(coordinator, entry),
                    VeeamLicenseExpirationSensor(coordinator, entry),
                    VeeamLicenseUsageSensor(coordinator, entry),
                ]
            )

        # Create server sensors
        if coordinator.data.get("server"):
            entities.extend(
                [
                    VeeamServerVersionSensor(coordinator, entry),
                ]
            )

        # Create sensors for each backup job (each job gets its own device)
        for job in coordinator.data.get("jobs", []):
            entities.append(VeeamJobSensor(coordinator, entry, job))

    async_add_entities(entities)


class VeeamLicenseStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam License Status sensor."""

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_license_status"
        self._attr_name = "License Status"
        self._attr_icon = "mdi:license"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self.coordinator.data.get("license"):
            return self.coordinator.data["license"].get("status", "unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if self.coordinator.data and self.coordinator.data.get("license"):
            license_data = self.coordinator.data["license"]
            return {
                "license_id": license_data.get("license_id"),
                "licensed_to": license_data.get("licensed_to"),
                "type": license_data.get("type"),
                "expiration_date": license_data.get("expiration_date"),
            }
        return {}

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_license")},
            "name": "Veeam 365 License",
            "manufacturer": "Veeam",
            "model": "License Information",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }


class VeeamLicenseTypeSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam License Type sensor."""

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_license_type"
        self._attr_name = "License Type"
        self._attr_icon = "mdi:card-account-details"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self.coordinator.data.get("license"):
            return self.coordinator.data["license"].get("type")
        return None

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_license")},
            "name": "Veeam 365 License",
            "manufacturer": "Veeam",
            "model": "License Information",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }


class VeeamLicenseExpirationSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam License Expiration sensor."""

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_license_expiration"
        self._attr_name = "License Expiration"
        self._attr_icon = "mdi:calendar-clock"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self.coordinator.data.get("license"):
            return self.coordinator.data["license"].get("expiration_date")
        return None

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_license")},
            "name": "Veeam 365 License",
            "manufacturer": "Veeam",
            "model": "License Information",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }


class VeeamLicenseUsageSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam License Usage sensor."""

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_license_usage"
        self._attr_name = "License Usage"
        self._attr_icon = "mdi:account-multiple"
        self._attr_native_unit_of_measurement = "users"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self.coordinator.data.get("license"):
            return self.coordinator.data["license"].get("used_users")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if self.coordinator.data and self.coordinator.data.get("license"):
            license_data = self.coordinator.data["license"]
            return {
                "total_users": license_data.get("total_users"),
                "used_users": license_data.get("used_users"),
                "new_users": license_data.get("new_users"),
            }
        return {}

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_license")},
            "name": "Veeam 365 License",
            "manufacturer": "Veeam",
            "model": "License Information",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }


class VeeamServerVersionSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam Server Version sensor."""

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_server_version"
        self._attr_name = "Server Version"
        self._attr_icon = "mdi:server"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self.coordinator.data.get("server"):
            return self.coordinator.data["server"].get("version")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if self.coordinator.data and self.coordinator.data.get("server"):
            server_data = self.coordinator.data["server"]
            return {
                "version": server_data.get("version"),
                "build": server_data.get("build"),
            }
        return {}

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_server")},
            "name": "Veeam 365 Server",
            "manufacturer": "Veeam",
            "model": "Backup for Microsoft 365",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }


class VeeamJobSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam Backup Job sensor."""

    def __init__(self, coordinator, config_entry, job_data):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id", job_data.get("name"))
        self._job_name = job_data.get("name", "Unknown Job")

        # Set unique ID
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}"
        self._attr_name = "Status"

    def _find_job_data(self) -> dict[str, Any] | None:
        """Find the job data for this sensor from coordinator data."""
        if not self.coordinator.data or not self.coordinator.data.get("jobs"):
            return None

        for job in self.coordinator.data["jobs"]:
            job_id = job.get("id", job.get("name"))
            if job_id == self._job_id:
                return job

        return None

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        job_data = self._find_job_data()
        if job_data:
            return job_data.get("status", "unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        job_data = self._find_job_data()
        if job_data:
            return {
                "job_id": job_data.get("id"),
                "job_name": job_data.get("name"),
                "backup_type": job_data.get("backup_type"),
                "last_run": job_data.get("last_run"),
                "next_run": job_data.get("next_run"),
                "is_enabled": job_data.get("is_enabled"),
                "total_objects": job_data.get("total_objects"),
                "processed_objects": job_data.get("processed_objects"),
            }

        return {}

    @property
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        state = self.native_value
        if state == "running":
            return "mdi:backup-restore"
        elif state == "success":
            return "mdi:check-circle"
        elif state == "warning":
            return "mdi:alert"
        elif state == "failed":
            return "mdi:close-circle"
        return "mdi:cloud-sync"

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, f"{self._config_entry.entry_id}_job_{self._job_id}")},
            "name": f"{self._job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Job",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }
