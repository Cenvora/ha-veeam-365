"""Support for Veeam Backup & Replication buttons."""

from __future__ import annotations

import asyncio
import importlib
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    DEFAULT_API_VERSION,
    DOMAIN,
    check_api_feature_availability,
)

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming the Veeam API
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam Backup & Replication buttons."""
    coordinator = entry.runtime_data["coordinator"]
    veeam_client = entry.runtime_data["veeam_client"]

    added_repository_ids: set[str] = set()
    added_job_ids: set[str] = set()
    added_copy_job_ids: set[str] = set()

    @callback
    def _sync_entities() -> None:
        if not coordinator.data:
            return

        # Get the configured API version
        api_version = entry.options.get(
            CONF_API_VERSION,
            entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )

        new_entities = []

        # Create buttons for each job
        for job in coordinator.data.get("jobs", []):
            job_id = job.get("id")
            if not job_id or job_id in added_job_ids:
                continue

            job_buttons = []

            # Check if each button type's API feature is available before creating
            if check_api_feature_availability(api_version, "models.job_start_action"):
                job_buttons.append(VeeamJobStartButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_stop_action"):
                job_buttons.append(VeeamJobStopButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_retry_action"):
                job_buttons.append(VeeamJobRetryButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_enable_action"):
                job_buttons.append(VeeamJobEnableButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_disable_action"):
                job_buttons.append(VeeamJobDisableButton(coordinator, entry, job, veeam_client))

            new_entities.extend(job_buttons)
            added_job_ids.add(job_id)
            _LOGGER.debug(
                "Adding %d buttons for job: %s (id: %s)",
                len(job_buttons),
                job.get("name"),
                job_id,
            )

        # Create buttons for each copy job
        for copy_job in coordinator.data.get("copy_jobs", []):
            copy_job_id = copy_job.get("id")
            if not copy_job_id or copy_job_id in added_copy_job_ids:
                continue

            copy_job_buttons = []

            # Check if each button type's API feature is available before creating
            if check_api_feature_availability(api_version, "models.copy_job_start_action"):
                copy_job_buttons.append(
                    VeeamCopyJobStartButton(coordinator, entry, copy_job, veeam_client)
                )

            if check_api_feature_availability(api_version, "models.copy_job_stop_action"):
                copy_job_buttons.append(
                    VeeamCopyJobStopButton(coordinator, entry, copy_job, veeam_client)
                )

            if check_api_feature_availability(api_version, "models.copy_job_enable_action"):
                copy_job_buttons.append(
                    VeeamCopyJobEnableButton(coordinator, entry, copy_job, veeam_client)
                )

            if check_api_feature_availability(api_version, "models.copy_job_disable_action"):
                copy_job_buttons.append(
                    VeeamCopyJobDisableButton(coordinator, entry, copy_job, veeam_client)
                )

            new_entities.extend(copy_job_buttons)
            added_copy_job_ids.add(copy_job_id)
            _LOGGER.debug(
                "Adding %d buttons for copy job: %s (id: %s)",
                len(copy_job_buttons),
                copy_job.get("name"),
                copy_job_id,
            )

        # Create synchronize button for each repository
        if check_api_feature_availability(
            api_version, "models.backup_repository_start_synchronize_action"
        ):
            for repository in coordinator.data.get("repositories", []):
                repo_id = repository.get("id")
                if not repo_id or repo_id in added_repository_ids:
                    continue

                new_entities.append(
                    VeeamRepositoryRescanButton(coordinator, entry, repository, veeam_client)
                )
                added_repository_ids.add(repo_id)
                _LOGGER.debug(
                    "Adding synchronize button for repository: %s (id: %s)",
                    repository.get("name"),
                    repo_id,
                )

        if new_entities:
            _LOGGER.debug("Adding %d Veeam buttons", len(new_entities))
            async_add_entities(new_entities)

        # Remove stale button entities
        _remove_stale_button_entities(
            hass, entry, added_repository_ids, added_job_ids, added_copy_job_ids
        )

    def _remove_stale_button_entities(
        hass: HomeAssistant,
        entry: ConfigEntry,
        current_repo_ids: set[str],
        current_job_ids: set[str],
        current_copy_job_ids: set[str],
    ) -> None:
        """Remove button entities for repos/jobs/copy jobs that no longer exist."""
        if not coordinator.data:
            return

        entity_reg = er.async_get(hass)

        # Get current IDs from coordinator data
        current_repos_in_data = {
            repo.get("id") for repo in coordinator.data.get("repositories", []) if repo.get("id")
        }
        current_jobs_in_data = {
            job.get("id") for job in coordinator.data.get("jobs", []) if job.get("id")
        }
        current_copy_jobs_in_data = {
            copy_job.get("id")
            for copy_job in coordinator.data.get("copy_jobs", [])
            if copy_job.get("id")
        }

        # Find stale repository buttons
        stale_repo_ids = current_repo_ids - current_repos_in_data
        for repo_id in stale_repo_ids:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"repository_{repo_id}_rescan" in entity.unique_id:
                    _LOGGER.info("Removing stale repository button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            current_repo_ids.discard(repo_id)

        # Find stale job buttons
        stale_job_ids = current_job_ids - current_jobs_in_data
        for job_id in stale_job_ids:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                # Match pattern: {entry_id}_job_{job_id}_{action}
                # Avoid matching: {entry_id}_copy_job_{job_id}_{action}
                if entity.unique_id and f"_job_{job_id}_" in entity.unique_id:
                    _LOGGER.info("Removing stale job button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            current_job_ids.discard(job_id)

        # Find stale copy job buttons
        stale_copy_job_ids = current_copy_job_ids - current_copy_jobs_in_data
        for copy_job_id in stale_copy_job_ids:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"copy_job_{copy_job_id}" in entity.unique_id:
                    _LOGGER.info("Removing stale copy job button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            current_copy_job_ids.discard(copy_job_id)

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    coordinator.async_add_listener(_sync_entities)


class VeeamRepositoryRescanButton(CoordinatorEntity, ButtonEntity):
    """Button to trigger repository synchronize."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, repository_data, veeam_client):
        """Initialize the synchronize button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._repo_id = repository_data.get("id")
        self._repo_name = repository_data.get("name", "Unknown Repository")
        self._veeam_client = veeam_client
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_rescan"
        self._attr_name = "Synchronize Cache"

    @property
    def device_info(self):
        """Return device info for this repository."""
        return {
            "identifiers": {(DOMAIN, f"repository_{self._repo_id}")},
            "name": f"{self._repo_name}",
            "manufacturer": "Veeam",
            "model": "Backup Repository",
        }

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:sync"

    async def async_press(self) -> None:
        """Handle the button press to trigger a repository synchronize.

        This method calls the Veeam API to synchronize the repository using the
        veeam_365 library's synchronize endpoint with the repository ID.
        After a successful synchronize request, it triggers a coordinator refresh
        to update all repository sensors.

        Side effects:
            - Calls the Veeam API repositories synchronize endpoint via veeam_365 library
            - Triggers coordinator.async_request_refresh() on success
        """
        try:
            # Get the API version
            api_version = self._config_entry.options.get(
                CONF_API_VERSION,
                self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
            )
            api_module = API_VERSIONS.get(api_version, "v8")

            # VeeamClient handles token refresh automatically - no manual check needed

            # Trigger the synchronize using veeam_365 library VeeamClient
            try:
                # Import the body model for the synchronize request
                models_module = await asyncio.to_thread(
                    importlib.import_module,
                    f"veeam_365.{api_module}.models." f"backup_repository_start_synchronize_action",
                )
                BackupRepositoryStartSynchronizeAction = (
                    models_module.BackupRepositoryStartSynchronizeAction
                )
                body = BackupRepositoryStartSynchronizeAction()
            except (ImportError, AttributeError) as e:
                _LOGGER.error(
                    "Failed to import BackupRepositoryStartSynchronizeAction: %s. "
                    "Cannot synchronize repository.",
                    e,
                )
                return

            # Call the synchronize endpoint using VeeamClient
            try:
                repositories_api = await asyncio.to_thread(
                    self._veeam_client.api, "backup_repository"
                )
                await self._veeam_client.call(
                    repositories_api.backup_repository_start_synchronize,
                    id=self._repo_id,
                    body=body,
                )
                _LOGGER.info(
                    "Successfully triggered synchronize for repository: %s", self._repo_name
                )
                # Request coordinator update to refresh repository state
                await self.coordinator.async_request_refresh()
            except Exception as call_err:
                _LOGGER.error(
                    "Failed to synchronize repository %s: %s",
                    self._repo_name,
                    call_err,
                )
                raise

        except Exception as err:
            _LOGGER.error("Error synchronizing repository %s: %s", self._repo_name, err)
            raise


# ===========================
# JOB BUTTONS
# ===========================


class VeeamJobButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for Veeam job buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the job button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")
        self._veeam_client = veeam_client

    @property
    def device_info(self):
        """Return device info for this job."""
        return {
            "identifiers": {(DOMAIN, f"job_{self._job_id}")},
            "name": f"{self._job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Job",
        }

    def _get_api_module(self) -> str:
        """Get the API module name based on the configured API version."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        return API_VERSIONS.get(api_version, "v8")

    async def _import_spec_model(self, spec_name: str):
        """Import a spec model from the veeam_365 library.

        Args:
            spec_name: Name of the spec model (e.g., 'job_start_action', 'job_stop_action')

        Returns:
            The spec model class

        Raises:
            ImportError: If the model cannot be imported
            AttributeError: If the model class cannot be found
        """
        api_module = self._get_api_module()
        models_module = await asyncio.to_thread(
            importlib.import_module, f"veeam_365.{api_module}.models.{spec_name}"
        )
        # Convert snake_case to PascalCase for class name
        class_name = "".join(word.capitalize() for word in spec_name.split("_"))
        return getattr(models_module, class_name)


class VeeamJobStartButton(VeeamJobButtonBase):
    """Button to start a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_start"
        self._attr_name = "Start"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:play"

    async def async_press(self) -> None:
        """Handle the button press to start the job."""
        # Import the body model for the start request
        try:
            JobStartAction = await self._import_spec_model("job_start_action")
            body = JobStartAction(perform_active_full=False)
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStartAction: %s. Cannot start job.", e)
            return

        # Call the start endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "job")
            await self._veeam_client.call(
                jobs_api.job_start,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully started job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to start job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobStopButton(VeeamJobButtonBase):
    """Button to stop a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_stop"
        self._attr_name = "Stop"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:stop"

    async def async_press(self) -> None:
        """Handle the button press to stop the job."""
        # Import the body model for the stop request
        try:
            JobStopAction = await self._import_spec_model("job_stop_action")
            # JobStopAction typically has no required parameters
            body = JobStopAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStopAction: %s. Cannot stop job.", e)
            return

        # Call the stop endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "job")
            await self._veeam_client.call(
                jobs_api.job_stop,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully stopped job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to stop job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobRetryButton(VeeamJobButtonBase):
    """Button to retry a failed Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_retry"
        self._attr_name = "Retry"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:refresh"

    async def async_press(self) -> None:
        """Handle the button press to retry the job."""
        # Import the body model for the retry request
        try:
            JobRetryAction = await self._import_spec_model("job_retry_action")
            # JobRetryAction typically has no required parameters
            body = JobRetryAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobRetryAction: %s. Cannot retry job.", e)
            return

        # Call the retry endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "job")
            await self._veeam_client.call(
                jobs_api.job_retry,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully retried job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to retry job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobEnableButton(VeeamJobButtonBase):
    """Button to enable a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_enable"
        self._attr_name = "Enable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:check-circle-outline"

    async def async_press(self) -> None:
        """Handle the button press to enable the job."""
        # Import the body model for the enable request
        try:
            JobEnableAction = await self._import_spec_model("job_enable_action")
            body = JobEnableAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobEnableAction: %s. Cannot enable job.", e)
            return

        # Call the enable endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "job")
            await self._veeam_client.call(
                jobs_api.job_enable,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully enabled job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to enable job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobDisableButton(VeeamJobButtonBase):
    """Button to disable a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_disable"
        self._attr_name = "Disable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:cancel"

    async def async_press(self) -> None:
        """Handle the button press to disable the job."""
        # Import the body model for the disable request
        try:
            JobDisableAction = await self._import_spec_model("job_disable_action")
            body = JobDisableAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobDisableAction: %s. Cannot disable job.", e)
            return

        # Call the disable endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "job")
            await self._veeam_client.call(
                jobs_api.job_disable,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully disabled job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to disable job %s: %s",
                self._job_name,
                call_err,
            )
            raise


# ===========================
# COPY JOB BUTTONS
# ===========================


class VeeamCopyJobButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for Veeam copy job buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, copy_job_data, veeam_client):
        """Initialize the copy job button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._copy_job_id = copy_job_data.get("id")
        self._copy_job_name = copy_job_data.get("name", "Unknown Copy Job")
        self._veeam_client = veeam_client

    @property
    def device_info(self):
        """Return device info for this copy job."""
        return {
            "identifiers": {(DOMAIN, f"copy_job_{self._copy_job_id}")},
            "name": f"{self._copy_job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Copy Job",
        }

    def _get_api_module(self) -> str:
        """Get the API module name based on the configured API version."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        return API_VERSIONS.get(api_version, "v8")

    async def _import_spec_model(self, spec_name: str):
        """Import a spec model from the veeam_365 library.

        Args:
            spec_name: Name of the spec model
                (e.g., 'copy_job_start_action', 'copy_job_stop_action')

        Returns:
            The spec model class

        Raises:
            ImportError: If the model cannot be imported
            AttributeError: If the model class cannot be found
        """
        api_module = self._get_api_module()
        models_module = await asyncio.to_thread(
            importlib.import_module, f"veeam_365.{api_module}.models.{spec_name}"
        )
        # Convert snake_case to PascalCase for class name
        class_name = "".join(word.capitalize() for word in spec_name.split("_"))
        return getattr(models_module, class_name)


class VeeamCopyJobStartButton(VeeamCopyJobButtonBase):
    """Button to start a Veeam copy job."""

    def __init__(self, coordinator, config_entry, copy_job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, copy_job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_start"
        self._attr_name = "Start"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:play"

    async def async_press(self) -> None:
        """Handle the button press to start the copy job."""
        # Import the body model for the start request
        try:
            CopyJobStartAction = await self._import_spec_model("copy_job_start_action")
            body = CopyJobStartAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import CopyJobStartAction: %s. Cannot start copy job.", e)
            return

        # Call the start endpoint using VeeamClient
        try:
            copy_jobs_api = await asyncio.to_thread(self._veeam_client.api, "copy_job")
            await self._veeam_client.call(
                copy_jobs_api.copy_job_start,
                id=self._copy_job_id,
                body=body,
            )
            _LOGGER.info("Successfully started copy job: %s", self._copy_job_name)
            # Request coordinator update to refresh copy job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to start copy job %s: %s",
                self._copy_job_name,
                call_err,
            )
            raise


class VeeamCopyJobStopButton(VeeamCopyJobButtonBase):
    """Button to stop a Veeam copy job."""

    def __init__(self, coordinator, config_entry, copy_job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, copy_job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_stop"
        self._attr_name = "Stop"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:stop"

    async def async_press(self) -> None:
        """Handle the button press to stop the copy job."""
        # Import the body model for the stop request
        try:
            CopyJobStopAction = await self._import_spec_model("copy_job_stop_action")
            body = CopyJobStopAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import CopyJobStopAction: %s. Cannot stop copy job.", e)
            return

        # Call the stop endpoint using VeeamClient
        try:
            copy_jobs_api = await asyncio.to_thread(self._veeam_client.api, "copy_job")
            await self._veeam_client.call(
                copy_jobs_api.copy_job_stop,
                id=self._copy_job_id,
                body=body,
            )
            _LOGGER.info("Successfully stopped copy job: %s", self._copy_job_name)
            # Request coordinator update to refresh copy job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to stop copy job %s: %s",
                self._copy_job_name,
                call_err,
            )
            raise


class VeeamCopyJobEnableButton(VeeamCopyJobButtonBase):
    """Button to enable a Veeam copy job."""

    def __init__(self, coordinator, config_entry, copy_job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, copy_job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_enable"
        self._attr_name = "Enable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:check-circle-outline"

    async def async_press(self) -> None:
        """Handle the button press to enable the copy job."""
        # Import the body model for the enable request
        try:
            CopyJobEnableAction = await self._import_spec_model("copy_job_enable_action")
            body = CopyJobEnableAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import CopyJobEnableAction: %s. Cannot enable copy job.", e)
            return

        # Call the enable endpoint using VeeamClient
        try:
            copy_jobs_api = await asyncio.to_thread(self._veeam_client.api, "copy_job")
            await self._veeam_client.call(
                copy_jobs_api.copy_job_enable,
                id=self._copy_job_id,
                body=body,
            )
            _LOGGER.info("Successfully enabled copy job: %s", self._copy_job_name)
            # Request coordinator update to refresh copy job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to enable copy job %s: %s",
                self._copy_job_name,
                call_err,
            )
            raise


class VeeamCopyJobDisableButton(VeeamCopyJobButtonBase):
    """Button to disable a Veeam copy job."""

    def __init__(self, coordinator, config_entry, copy_job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, copy_job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_copy_job_{self._copy_job_id}_disable"
        self._attr_name = "Disable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:cancel"

    async def async_press(self) -> None:
        """Handle the button press to disable the copy job."""
        # Import the body model for the disable request
        try:
            CopyJobDisableAction = await self._import_spec_model("copy_job_disable_action")
            body = CopyJobDisableAction()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import CopyJobDisableAction: %s. Cannot disable copy job.", e)
            return

        # Call the disable endpoint using VeeamClient
        try:
            copy_jobs_api = await asyncio.to_thread(self._veeam_client.api, "copy_job")
            await self._veeam_client.call(
                copy_jobs_api.copy_job_disable,
                id=self._copy_job_id,
                body=body,
            )
            _LOGGER.info("Successfully disabled copy job: %s", self._copy_job_name)
            # Request coordinator update to refresh copy job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to disable copy job %s: %s",
                self._copy_job_name,
                call_err,
            )
            raise
