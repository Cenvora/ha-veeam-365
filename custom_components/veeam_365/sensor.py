"""Sensor platform for Veeam Backup for Microsoft 365."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
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
    """Set up Veeam 365 sensor based on a config entry."""
    coordinator = entry.runtime_data["coordinator"]

    entities = []

    # Add job sensors
    if coordinator.data and "jobs" in coordinator.data:
        for job in coordinator.data["jobs"]:
            entities.extend(
                [
                    Veeam365JobStatusSensor(coordinator, job),
                    Veeam365JobLastRunSensor(coordinator, job),
                    Veeam365JobNextRunSensor(coordinator, job),
                ]
            )

    # Add organization sensors
    if coordinator.data and "organizations" in coordinator.data:
        for org in coordinator.data["organizations"]:
            entities.append(Veeam365OrganizationSensor(coordinator, org))

    # Add repository sensors
    if coordinator.data and "repositories" in coordinator.data:
        for repo in coordinator.data["repositories"]:
            entities.append(Veeam365RepositorySensor(coordinator, repo))

    # Add license sensors
    if coordinator.data and "license_info" in coordinator.data and coordinator.data["license_info"]:
        entities.extend(
            [
                Veeam365LicenseStatusSensor(coordinator),
                Veeam365LicenseExpirationSensor(coordinator),
                Veeam365LicenseUsersSensor(coordinator),
            ]
        )

    # Add diagnostic sensors
    if coordinator.data and "diagnostics" in coordinator.data:
        entities.append(Veeam365LastPollSensor(coordinator))

    async_add_entities(entities)


class Veeam365SensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Veeam 365 sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_id: str, device_name: str, device_model: str):
        """Initialize the sensor."""
        super().__init__(coordinator)
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


class Veeam365JobStatusSensor(Veeam365SensorBase):
    """Sensor for job status."""

    def __init__(self, coordinator, job):
        """Initialize the sensor."""
        super().__init__(coordinator, f"job_{job['id']}", job["name"], "Backup Job")
        self._job_id = job["id"]
        self._attr_unique_id = f"{DOMAIN}_{job['id']}_status"
        self._attr_translation_key = "job_status"

    @property
    def native_value(self):
        """Return the state."""
        if not self.coordinator.data or "jobs" not in self.coordinator.data:
            return None

        for job in self.coordinator.data["jobs"]:
            if job["id"] == self._job_id:
                return job.get("last_status", "unknown")
        return None

    @property
    def icon(self):
        """Return icon based on status."""
        status = self.native_value
        if status == "success":
            return "mdi:check-circle"
        elif status == "running":
            return "mdi:play-circle"
        elif status == "failed":
            return "mdi:alert-circle"
        elif status == "warning":
            return "mdi:alert"
        return "mdi:help-circle"

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        if not self.coordinator.data or "jobs" not in self.coordinator.data:
            return {}

        for job in self.coordinator.data["jobs"]:
            if job["id"] == self._job_id:
                return {
                    "job_id": job["id"],
                    "is_enabled": job.get("is_enabled", True),
                    "description": job.get("description", ""),
                }
        return {}


class Veeam365JobLastRunSensor(Veeam365SensorBase):
    """Sensor for job last run time."""

    def __init__(self, coordinator, job):
        """Initialize the sensor."""
        super().__init__(coordinator, f"job_{job['id']}", job["name"], "Backup Job")
        self._job_id = job["id"]
        self._attr_unique_id = f"{DOMAIN}_{job['id']}_last_run"
        self._attr_translation_key = "job_last_run"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        """Return the state."""
        if not self.coordinator.data or "jobs" not in self.coordinator.data:
            return None

        for job in self.coordinator.data["jobs"]:
            if job["id"] == self._job_id:
                return job.get("last_run")
        return None


class Veeam365JobNextRunSensor(Veeam365SensorBase):
    """Sensor for job next run time."""

    def __init__(self, coordinator, job):
        """Initialize the sensor."""
        super().__init__(coordinator, f"job_{job['id']}", job["name"], "Backup Job")
        self._job_id = job["id"]
        self._attr_unique_id = f"{DOMAIN}_{job['id']}_next_run"
        self._attr_translation_key = "job_next_run"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        """Return the state."""
        if not self.coordinator.data or "jobs" not in self.coordinator.data:
            return None

        for job in self.coordinator.data["jobs"]:
            if job["id"] == self._job_id:
                return job.get("next_run")
        return None


class Veeam365OrganizationSensor(Veeam365SensorBase):
    """Sensor for organization."""

    def __init__(self, coordinator, org):
        """Initialize the sensor."""
        super().__init__(coordinator, f"org_{org['id']}", org["name"], "Microsoft 365 Organization")
        self._org_id = org["id"]
        self._attr_unique_id = f"{DOMAIN}_{org['id']}_status"
        self._attr_translation_key = "organization_status"

    @property
    def native_value(self):
        """Return the state."""
        if not self.coordinator.data or "organizations" not in self.coordinator.data:
            return None

        for org in self.coordinator.data["organizations"]:
            if org["id"] == self._org_id:
                return "enabled" if org.get("is_backup_enabled") else "disabled"
        return None

    @property
    def icon(self):
        """Return icon."""
        return "mdi:office-building"

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        if not self.coordinator.data or "organizations" not in self.coordinator.data:
            return {}

        for org in self.coordinator.data["organizations"]:
            if org["id"] == self._org_id:
                return {
                    "organization_id": org["id"],
                    "region": org.get("region", "Unknown"),
                    "is_backup_enabled": org.get("is_backup_enabled", False),
                }
        return {}


class Veeam365RepositorySensor(Veeam365SensorBase):
    """Sensor for repository."""

    def __init__(self, coordinator, repo):
        """Initialize the sensor."""
        super().__init__(coordinator, f"repo_{repo['id']}", repo["name"], "Backup Repository")
        self._repo_id = repo["id"]
        self._attr_unique_id = f"{DOMAIN}_{repo['id']}_type"
        self._attr_translation_key = "repository_type"

    @property
    def native_value(self):
        """Return the state."""
        if not self.coordinator.data or "repositories" not in self.coordinator.data:
            return None

        for repo in self.coordinator.data["repositories"]:
            if repo["id"] == self._repo_id:
                return repo.get("type", "unknown")
        return None

    @property
    def icon(self):
        """Return icon."""
        return "mdi:database"

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        if not self.coordinator.data or "repositories" not in self.coordinator.data:
            return {}

        for repo in self.coordinator.data["repositories"]:
            if repo["id"] == self._repo_id:
                return {
                    "repository_id": repo["id"],
                    "description": repo.get("description", ""),
                }
        return {}


class Veeam365LicenseStatusSensor(Veeam365SensorBase):
    """Sensor for license status."""

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator, "license", "License", "License")
        self._attr_unique_id = f"{DOMAIN}_license_status"
        self._attr_translation_key = "license_status"

    @property
    def native_value(self):
        """Return the state."""
        if (
            not self.coordinator.data
            or "license_info" not in self.coordinator.data
            or not self.coordinator.data["license_info"]
        ):
            return None

        return self.coordinator.data["license_info"].get("status", "unknown")

    @property
    def icon(self):
        """Return icon based on status."""
        status = self.native_value
        if status == "valid":
            return "mdi:certificate"
        elif status == "expired":
            return "mdi:certificate-outline"
        return "mdi:alert-circle"


class Veeam365LicenseExpirationSensor(Veeam365SensorBase):
    """Sensor for license expiration."""

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator, "license", "License", "License")
        self._attr_unique_id = f"{DOMAIN}_license_expiration"
        self._attr_translation_key = "license_expiration"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        """Return the state."""
        if (
            not self.coordinator.data
            or "license_info" not in self.coordinator.data
            or not self.coordinator.data["license_info"]
        ):
            return None

        return self.coordinator.data["license_info"].get("expiration_date")


class Veeam365LicenseUsersSensor(Veeam365SensorBase):
    """Sensor for licensed users."""

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator, "license", "License", "License")
        self._attr_unique_id = f"{DOMAIN}_license_users"
        self._attr_translation_key = "license_users"
        self._attr_native_unit_of_measurement = "users"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        """Return the state."""
        if (
            not self.coordinator.data
            or "license_info" not in self.coordinator.data
            or not self.coordinator.data["license_info"]
        ):
            return None

        return self.coordinator.data["license_info"].get("used_users", 0)

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        if (
            not self.coordinator.data
            or "license_info" not in self.coordinator.data
            or not self.coordinator.data["license_info"]
        ):
            return {}

        license_info = self.coordinator.data["license_info"]
        return {
            "licensed_users": license_info.get("licensed_users", 0),
            "used_users": license_info.get("used_users", 0),
        }


class Veeam365LastPollSensor(Veeam365SensorBase):
    """Sensor for last successful poll time."""

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator, "server", "Veeam 365 Server", "Server")
        self._attr_unique_id = f"{DOMAIN}_last_poll"
        self._attr_translation_key = "server_last_poll"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        """Return the state."""
        if not self.coordinator.data or "diagnostics" not in self.coordinator.data:
            return None

        return self.coordinator.data["diagnostics"].get("last_successful_poll")
