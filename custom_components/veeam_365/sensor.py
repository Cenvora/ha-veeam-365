"""Support for Veeam Backup for Microsoft 365 sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, check_api_feature_availability, configured_api_version

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming the Veeam API
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data["coordinator"]

    added_job_ids: set[str] = set()
    added_copy_job_ids: set[str] = set()
    added_repository_ids: set[str] = set()
    server_added = False
    license_added = False

    @callback
    def _sync_entities() -> None:
        nonlocal server_added
        nonlocal license_added

        if not coordinator.data:
            return

        # The version setup actually resolved, which is not the stored value when that is
        # "auto"
        api_version = configured_api_version(entry)

        new_entities = []

        # ---- JOB SENSORS (dynamic) - Each job becomes a device with multiple sensors ----
        # Jobs data comes from jobs API, only create if available
        if check_api_feature_availability(api_version, "api.job"):
            for job in coordinator.data.get("jobs", []):
                job_id = job.get("id")
                if not job_id or job_id in added_job_ids:
                    continue

                # Create sensors for each job attribute
                new_entities.extend(
                    [
                        VeeamJobNameSensor(coordinator, entry, job),
                        VeeamJobTypeSensor(coordinator, entry, job),
                        VeeamJobLastRunSensor(coordinator, entry, job),
                        VeeamJobNextRunSensor(coordinator, entry, job),
                        VeeamJobLastBackupSensor(coordinator, entry, job),
                        VeeamJobEnabledSensor(coordinator, entry, job),
                        VeeamJobLastStatusSensor(coordinator, entry, job),
                    ]
                )
                added_job_ids.add(job_id)

        # ---- COPY JOB SENSORS (dynamic) - Each copy job becomes a device with multiple sensors ----
        # Copy jobs data comes from copy_jobs API, only create if available
        if check_api_feature_availability(api_version, "api.copy_job"):
            for copy_job in coordinator.data.get("copy_jobs", []):
                copy_job_id = copy_job.get("id")
                if not copy_job_id or copy_job_id in added_copy_job_ids:
                    continue

                # Create sensors for each copy job attribute
                new_entities.extend(
                    [
                        VeeamCopyJobNameSensor(coordinator, entry, copy_job),
                        VeeamCopyJobLastRunSensor(coordinator, entry, copy_job),
                        VeeamCopyJobLastBackupSensor(coordinator, entry, copy_job),
                        VeeamCopyJobEnabledSensor(coordinator, entry, copy_job),
                        VeeamCopyJobLastStatusSensor(coordinator, entry, copy_job),
                    ]
                )
                added_copy_job_ids.add(copy_job_id)

        # ---- REPOSITORY SENSORS (dynamic) - Each repository becomes a device with multiple sensors ----
        # Repository data comes from repositories API, only create if available
        if check_api_feature_availability(api_version, "api.backup_repository"):
            for repository in coordinator.data.get("repositories", []):
                repo_id = repository.get("id")
                if not repo_id or repo_id in added_repository_ids:
                    continue

                # Create sensors for each repository attribute
                new_entities.extend(
                    [
                        VeeamRepositoryTypeSensor(coordinator, entry, repository),
                        VeeamRepositoryDescriptionSensor(coordinator, entry, repository),
                        VeeamRepositoryUsedSpaceSensor(coordinator, entry, repository),
                    ]
                )

                # Add immutability days sensor only if immutability is enabled and has days set
                if (
                    repository.get("is_immutable")
                    and repository.get("immutability_days") is not None
                ):
                    new_entities.append(
                        VeeamRepositoryImmutabilityDaysSensor(coordinator, entry, repository)
                    )
                added_repository_ids.add(repo_id)
                _LOGGER.debug(
                    "Adding repository sensors for: %s (id: %s)",
                    repository.get("name"),
                    repo_id,
                )

        # ---- SERVER SENSORS (once) - Server device with diagnostic and version sensors ----
        # Server info comes from ServiceInstance endpoint and diagnostics
        if not server_added and coordinator.data:
            new_entities.extend(
                [
                    VeeamServerVersionSensor(coordinator, entry),
                    VeeamServerInstallationIDSensor(coordinator, entry),
                    VeeamServerLastSuccessfulPollSensor(coordinator, entry),
                ]
            )
            server_added = True

        # ---- LICENSE SENSORS (once) - License becomes a device with multiple sensors ----
        # License data comes from license_ API, only create if available
        if (
            not license_added
            and coordinator.data.get("license_info")
            and check_api_feature_availability(api_version, "api.license_")
        ):
            new_entities.extend(
                [
                    VeeamLicenseStatusSensor(coordinator, entry),
                    VeeamLicenseTypeSensor(coordinator, entry),
                    VeeamLicenseExpirationSensor(coordinator, entry),
                    VeeamLicenseGracePeriodExpirationSensor(coordinator, entry),
                    VeeamLicenseLicensedToSensor(coordinator, entry),
                    VeeamLicenseTotalNumberSensor(coordinator, entry),
                    VeeamLicenseUsedNumberSensor(coordinator, entry),
                    VeeamLicenseNewNumberSensor(coordinator, entry),
                ]
            )
            license_added = True

        if new_entities:
            _LOGGER.debug("Adding %d Veeam sensors", len(new_entities))
            async_add_entities(new_entities)

        # Remove stale entities (jobs/repos that no longer exist)
        _remove_stale_entities(hass, entry, added_job_ids, added_copy_job_ids, added_repository_ids)

    def _remove_stale_entities(
        hass: HomeAssistant,
        entry: ConfigEntry,
        current_job_ids: set[str],
        current_copy_job_ids: set[str],
        current_repo_ids: set[str],
    ) -> None:
        """Remove entities for jobs/copy jobs/repos that no longer exist."""
        if not coordinator.data:
            return

        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)

        # Get current IDs from coordinator data
        current_jobs_in_data = {
            job.get("id") for job in coordinator.data.get("jobs", []) if job.get("id")
        }
        current_copy_jobs_in_data = {
            copy_job.get("id")
            for copy_job in coordinator.data.get("copy_jobs", [])
            if copy_job.get("id")
        }
        current_repos_in_data = {
            repo.get("id") for repo in coordinator.data.get("repositories", []) if repo.get("id")
        }

        # An empty collection is indistinguishable from a fetch that failed and degraded
        # gracefully, so pruning on empty would delete every job device the first time the
        # jobs endpoint errored. Deleting the last job of a kind is left to the per-device
        # Delete button, which async_remove_config_entry_device now allows.
        prunable = {
            "job": bool(current_jobs_in_data),
            "copy job": bool(current_copy_jobs_in_data),
            "repository": bool(current_repos_in_data),
        }
        for kind, allowed in prunable.items():
            if not allowed:
                _LOGGER.debug(
                    "Not pruning %s devices: nothing reported this cycle, which may be a "
                    "failed fetch rather than a deletion",
                    kind,
                )

        # Find stale job entities
        stale_job_ids = (current_job_ids - current_jobs_in_data) if prunable["job"] else set()
        for job_id in stale_job_ids:
            # Remove all entities for this job
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"job_{job_id}" in entity.unique_id:
                    _LOGGER.info("Removing stale job entity: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            # Remove the device itself
            device = device_reg.async_get_device(identifiers={(DOMAIN, f"job_{job_id}")})
            if device:
                _LOGGER.info("Removing stale job device: %s", device.id)
                device_reg.async_remove_device(device.id)
            current_job_ids.discard(job_id)

        # Find stale copy job entities
        stale_copy_job_ids = (
            (current_copy_job_ids - current_copy_jobs_in_data) if prunable["copy job"] else set()
        )
        for copy_job_id in stale_copy_job_ids:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"copy_job_{copy_job_id}" in entity.unique_id:
                    _LOGGER.info("Removing stale copy job entity: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            # Remove the device itself
            device = device_reg.async_get_device(identifiers={(DOMAIN, f"copy_job_{copy_job_id}")})
            if device:
                _LOGGER.info("Removing stale copy job device: %s", device.id)
                device_reg.async_remove_device(device.id)
            current_copy_job_ids.discard(copy_job_id)

        # Find stale repository entities
        stale_repo_ids = (
            (current_repo_ids - current_repos_in_data) if prunable["repository"] else set()
        )
        for repo_id in stale_repo_ids:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"repository_{repo_id}" in entity.unique_id:
                    _LOGGER.info("Removing stale repository entity: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            # Remove the device itself
            device = device_reg.async_get_device(identifiers={(DOMAIN, f"repository_{repo_id}")})
            if device:
                _LOGGER.info("Removing stale repository device: %s", device.id)
                device_reg.async_remove_device(device.id)
            current_repo_ids.discard(repo_id)

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    coordinator.async_add_listener(_sync_entities)


# ===========================
# MIXINS (shared logic for base classes)
# ===========================


class VeeamLicenseMixin:
    """Mixin providing shared license-related functionality."""

    def __init__(self, coordinator, config_entry):
        """Initialize the mixin."""
        self._config_entry = config_entry

    def _license_info(self) -> dict[str, Any] | None:
        """Get license info from coordinator data."""
        return self.coordinator.data.get("license_info") if self.coordinator.data else None

    @property
    def device_info(self):
        """Return device info for the Veeam license."""
        return {
            "identifiers": {(DOMAIN, f"license_{self._config_entry.entry_id}")},
            "name": "Veeam License",
            "manufacturer": "Veeam",
            "model": "License",
        }


class VeeamRepositoryMixin:
    """Mixin providing shared repository-related functionality."""

    def __init__(self, coordinator, config_entry, repository_data):
        """Initialize the mixin."""
        self._config_entry = config_entry
        self._repo_id = repository_data.get("id")
        self._repo_name = repository_data.get("name", "Unknown Repository")

    def _repository(self) -> dict[str, Any] | None:
        """Get repository data from coordinator."""
        if not self.coordinator.data:
            return None
        for repo in self.coordinator.data.get("repositories", []):
            if repo.get("id") == self._repo_id:
                return repo
        return None

    @property
    def device_info(self):
        """Return device info for this repository."""
        return {
            "identifiers": {(DOMAIN, f"repository_{self._repo_id}")},
            "name": f"{self._repo_name}",
            "manufacturer": "Veeam",
            "model": "Backup Repository",
        }


# ===========================
# JOB SENSORS (device per job)
# ===========================


class VeeamJobBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Veeam Job sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")

    def _job(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        for job in self.coordinator.data.get("jobs", []):
            if job.get("id") == self._job_id:
                return job
        return None

    @property
    def device_info(self):
        """Return device info for this job."""
        return {
            "identifiers": {(DOMAIN, f"job_{self._job_id}")},
            "name": f"{self._job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Job",
        }


class VeeamJobNameSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Name."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_name"
        self._attr_name = "Name"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        job = self._job()
        return job.get("name") if job else None

    @property
    def icon(self) -> str:
        return "mdi:label"


class VeeamJobTypeSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Backup Type."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_backup_type"
        self._attr_name = "Backup Type"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        job = self._job()
        return job.get("backup_type") if job else None

    @property
    def icon(self) -> str:
        return "mdi:file-tree"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The unprettified API value, for automations that match exactly."""
        data = self._job()
        return {"raw_value": data.get("backup_type_raw") if data else None}


class VeeamJobLastRunSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Last Run."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_last_run"
        self._attr_name = "Last Run"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        job = self._job()
        return job.get("last_run") if job else None

    @property
    def icon(self) -> str:
        return "mdi:clock-start"


class VeeamJobNextRunSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Next Run."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_next_run"
        self._attr_name = "Next Run"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        job = self._job()
        return job.get("next_run") if job else None

    @property
    def icon(self) -> str:
        return "mdi:clock-end"


class VeeamJobLastBackupSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Last Backup."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_last_backup"
        self._attr_name = "Last Backup"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        job = self._job()
        return job.get("last_backup") if job else None

    @property
    def icon(self) -> str:
        return "mdi:backup-restore"


class VeeamJobEnabledSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Enabled Status."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_is_enabled"
        self._attr_name = "Enabled"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        job = self._job()
        if not job:
            return None
        is_enabled = job.get("is_enabled")
        return "Yes" if is_enabled else "No"

    @property
    def icon(self) -> str:
        job = self._job()
        if job and job.get("is_enabled"):
            return "mdi:play-circle"
        return "mdi:pause-circle"


class VeeamJobLastStatusSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Last Status."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_last_status"
        self._attr_name = "Last Status"

    @property
    def native_value(self) -> str | None:
        job = self._job()
        return job.get("last_status") if job else None

    @property
    def icon(self) -> str:
        state = self.native_value
        if not state:
            return "mdi:cloud-sync"
        state_lower = str(state).lower()
        if state_lower == "success":
            return "mdi:check-circle"
        if state_lower == "warning":
            return "mdi:alert"
        if state_lower == "failed":
            return "mdi:close-circle"
        if state_lower == "running":
            return "mdi:play"
        return "mdi:cloud-sync"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The unprettified API value, for automations that match exactly."""
        data = self._job()
        return {"raw_value": data.get("last_status_raw") if data else None}


# ===========================
# COPY JOB SENSORS (device per copy job)
# ===========================


class VeeamCopyJobBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Veeam Copy Job sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, copy_job_data):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._copy_job_id = copy_job_data.get("id")
        self._copy_job_name = copy_job_data.get("name", "Unknown Copy Job")

    def _copy_job(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        for copy_job in self.coordinator.data.get("copy_jobs", []):
            if copy_job.get("id") == self._copy_job_id:
                return copy_job
        return None

    @property
    def device_info(self):
        """Return device info for this copy job."""
        return {
            "identifiers": {(DOMAIN, f"copy_job_{self._copy_job_id}")},
            "name": f"{self._copy_job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Copy Job",
        }


class VeeamCopyJobNameSensor(VeeamCopyJobBaseSensor):
    """Sensor for Veeam Copy Job Name."""

    def __init__(self, coordinator, config_entry, copy_job_data):
        super().__init__(coordinator, config_entry, copy_job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_name"
        self._attr_name = "Name"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        copy_job = self._copy_job()
        return copy_job.get("name") if copy_job else None

    @property
    def icon(self) -> str:
        return "mdi:label"


class VeeamCopyJobLastRunSensor(VeeamCopyJobBaseSensor):
    """Sensor for Veeam Copy Job Last Run."""

    def __init__(self, coordinator, config_entry, copy_job_data):
        super().__init__(coordinator, config_entry, copy_job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_last_run"
        self._attr_name = "Last Run"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        copy_job = self._copy_job()
        return copy_job.get("last_run") if copy_job else None

    @property
    def icon(self) -> str:
        return "mdi:clock-start"


class VeeamCopyJobLastBackupSensor(VeeamCopyJobBaseSensor):
    """Sensor for Veeam Copy Job Last Backup."""

    def __init__(self, coordinator, config_entry, copy_job_data):
        super().__init__(coordinator, config_entry, copy_job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_last_backup"
        self._attr_name = "Last Backup"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        copy_job = self._copy_job()
        return copy_job.get("last_backup") if copy_job else None

    @property
    def icon(self) -> str:
        return "mdi:backup-restore"


class VeeamCopyJobEnabledSensor(VeeamCopyJobBaseSensor):
    """Sensor for Veeam Copy Job Enabled Status."""

    def __init__(self, coordinator, config_entry, copy_job_data):
        super().__init__(coordinator, config_entry, copy_job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_is_enabled"
        self._attr_name = "Enabled"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        copy_job = self._copy_job()
        if not copy_job:
            return None
        is_enabled = copy_job.get("is_enabled")
        return "Yes" if is_enabled else "No"

    @property
    def icon(self) -> str:
        copy_job = self._copy_job()
        if copy_job and copy_job.get("is_enabled"):
            return "mdi:play-circle"
        return "mdi:pause-circle"


class VeeamCopyJobLastStatusSensor(VeeamCopyJobBaseSensor):
    """Sensor for Veeam Copy Job Last Status."""

    def __init__(self, coordinator, config_entry, copy_job_data):
        super().__init__(coordinator, config_entry, copy_job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_last_status"
        self._attr_name = "Last Status"

    @property
    def native_value(self) -> str | None:
        copy_job = self._copy_job()
        return copy_job.get("last_status") if copy_job else None

    @property
    def icon(self) -> str:
        state = self.native_value
        if not state:
            return "mdi:cloud-sync"
        state_lower = str(state).lower()
        if state_lower == "success":
            return "mdi:check-circle"
        if state_lower == "warning":
            return "mdi:alert"
        if state_lower == "failed":
            return "mdi:close-circle"
        if state_lower == "running":
            return "mdi:play"
        return "mdi:cloud-sync"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The unprettified API value, for automations that match exactly."""
        data = self._copy_job()
        return {"raw_value": data.get("last_status_raw") if data else None}


# ===========================
# SERVER SENSORS (single device)
# ===========================


class VeeamServerBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Veeam Server sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config_entry = config_entry

    def _server_info(self) -> dict[str, Any] | None:
        return self.coordinator.data.get("server_info") if self.coordinator.data else None

    @property
    def device_info(self):
        """Return device info for the Veeam server."""
        return {
            "identifiers": {(DOMAIN, f"server_{self._config_entry.entry_id}")},
            "name": "Veeam Server",
            "manufacturer": "Veeam",
            "model": "Backup for Microsoft 365",
        }


class VeeamServerVersionSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server Product Version."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_version"
        self._attr_name = "Product Version"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("version") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:tag"


class VeeamServerInstallationIDSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server Installation ID."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_installation_id"
        self._attr_name = "Installation ID"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("installation_id") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:identifier"


class VeeamServerLastSuccessfulPollSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server Last Successful Poll."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_last_successful_poll"
        self._attr_name = "Last Successful Poll"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        diagnostics = self.coordinator.data.get("diagnostics")
        return diagnostics.get("last_successful_poll") if diagnostics else None

    @property
    def icon(self) -> str:
        return "mdi:clock-check"


# ===========================
# LICENSE SENSORS (single device)
# ===========================


class VeeamLicenseBaseSensor(VeeamLicenseMixin, CoordinatorEntity, SensorEntity):
    """Base class for Veeam License sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamLicenseMixin.__init__(self, coordinator, config_entry)


class VeeamLicenseStatusSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Status."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("status") if license_info else None

    @property
    def icon(self) -> str:
        state = self.native_value
        if state and state.lower() == "valid":
            return "mdi:license"
        if state and state.lower() == "expired":
            return "mdi:license-off"
        return "mdi:license"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The unprettified API value, for automations that match exactly."""
        data = self._license_info()
        return {"raw_value": data.get("status_raw") if data else None}


class VeeamLicenseTypeSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Type."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_type"
        self._attr_name = "Type"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("type") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:file-document"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The unprettified API value, for automations that match exactly."""
        data = self._license_info()
        return {"raw_value": data.get("type_raw") if data else None}


class VeeamLicenseExpirationSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Expiration Date."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_expiration"
        self._attr_name = "Expiration Date"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        license_info = self._license_info()
        return license_info.get("expiration_date") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:calendar-end"


class VeeamLicenseGracePeriodExpirationSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Grace Period Expiration Date."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_grace_period_expires"
        self._attr_name = "Grace Period Expiration"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        license_info = self._license_info()
        return license_info.get("grace_period_expires") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:calendar-clock"


class VeeamLicenseLicensedToSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Licensed To."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_licensed_to"
        self._attr_name = "Licensed To"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("licensed_to") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:account"


class VeeamLicenseTotalNumberSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Total Number."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_total_number"
        self._attr_name = "Total Licenses"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        license_info = self._license_info()
        if not license_info:
            return None
        return license_info.get("total_number")

    @property
    def icon(self) -> str:
        return "mdi:counter"


class VeeamLicenseUsedNumberSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Used Number."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_used_number"
        self._attr_name = "Used Licenses"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        license_info = self._license_info()
        if not license_info:
            return None
        return license_info.get("used_number")

    @property
    def icon(self) -> str:
        return "mdi:account-multiple-check"


class VeeamLicenseNewNumberSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License New Number."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_new_number"
        self._attr_name = "New Licenses"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        license_info = self._license_info()
        if not license_info:
            return None
        return license_info.get("new_number")

    @property
    def icon(self) -> str:
        return "mdi:account-multiple-plus"


# ===========================
# REPOSITORY SENSORS (device per repository)
# ===========================


class VeeamRepositoryBaseSensor(VeeamRepositoryMixin, CoordinatorEntity, SensorEntity):
    """Base class for Veeam Repository sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, repository_data):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamRepositoryMixin.__init__(self, coordinator, config_entry, repository_data)


class VeeamRepositoryTypeSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Type."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_type"
        self._attr_name = "Type"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        repo = self._repository()
        return repo.get("type", "unknown") if repo else None

    @property
    def icon(self) -> str:
        repo = self._repository()
        if not repo:
            return "mdi:database"

        # Matched on the raw API value, so a reworded label cannot change which icon shows
        repo_type = (repo.get("type_raw") or repo.get("type") or "").lower()
        if "linux" in repo_type:
            return "mdi:linux"
        if "win" in repo_type:
            return "mdi:microsoft-windows"
        if any(word in repo_type for word in ("cloud", "azure", "aws", "s3", "wasabi")):
            return "mdi:cloud"
        return "mdi:database"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The unprettified API value, for automations that match exactly."""
        data = self._repository()
        return {"raw_value": data.get("type_raw") if data else None}


class VeeamRepositoryDescriptionSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Description."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_description"
        self._attr_name = "Description"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        repo = self._repository()
        return repo.get("description", "") if repo else None

    @property
    def icon(self) -> str:
        return "mdi:text"


class VeeamRepositoryUsedSpaceSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Used Space."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_used_space"
        self._attr_name = "Used Space"
        self._attr_native_unit_of_measurement = "GB"
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        repo = self._repository()
        if not repo:
            return None
        return repo.get("used_space_gb")

    @property
    def icon(self) -> str:
        return "mdi:database-alert"


class VeeamRepositoryImmutabilityDaysSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Immutability Days."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_repository_{self._repo_id}_immutability_days"
        )
        self._attr_name = "Immutability Days"
        self._attr_native_unit_of_measurement = "days"

    @property
    def native_value(self) -> int | None:
        repo = self._repository()
        if not repo:
            return None
        return repo.get("immutability_days")

    @property
    def icon(self) -> str:
        return "mdi:calendar-lock"
