"""Support for Veeam Backup for Microsoft 365 buttons.

Every press either does what it says or raises HomeAssistantError, which the UI shows as an
error toast. Nothing is swallowed: a button that fails silently looks exactly like one that
worked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import httpx
from veeam_365.exceptions import VeeamAuthenticationError, VeeamError

from .const import ACTION_TIMEOUT, DOMAIN
from .coordinator import (
    ACTIVE_MAINTENANCE_STATUSES,
    describe_error,
    error_message,
    is_error_response,
    supports_repository_maintenance,
)
from .entity import VeeamItemEntity, async_track_items

_LOGGER = logging.getLogger(__name__)

# One action at a time against the server
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class VeeamButtonDescription(ButtonEntityDescription):
    """A button calling one operation on one item.

    ``key`` is the unique ID suffix, which existing entities are keyed on — never change it.
    """

    operation: str
    # Name of the operation's ID argument, which differs between endpoints. None when the
    # item is named in the request body instead.
    id_param: str | None
    # Exception translation key, and the placeholder it names the item with
    failure_key: str
    name_placeholder: str = "job_name"
    # Whether the operation starts a job and takes RESTStartJobOptions
    start_options: bool = False
    # Any other request body, built from the API version's models and the item ID
    body_fn: Callable[[Any, str], Any] | None = None
    # What to pass as id_param when it is not the item's own ID, looked up in the coordinator
    # data by item ID. Returning None means there is nothing to act on, and the press fails
    # with ``nothing_message``.
    target_fn: Callable[[dict[str, Any], str], str | None] | None = None
    nothing_message: str = ""


def _job_buttons(prefix: str, api: str, id_param: str) -> tuple[VeeamButtonDescription, ...]:
    """Start/Stop/Enable/Disable for jobs ("job") and copy jobs ("copy_job")."""
    operation = {
        "job": lambda verb: f"job.job_{verb}_action",
        "copy_job": lambda verb: f"copy_job.copy_job_{verb}",
    }[api]
    icons = {
        "start": "mdi:play",
        "stop": "mdi:stop",
        "enable": "mdi:check-circle-outline",
        "disable": "mdi:cancel",
    }
    return tuple(
        VeeamButtonDescription(
            key=verb,
            translation_key=f"{prefix}_{verb}",
            entity_category=EntityCategory.CONFIG,
            icon=icon,
            operation=operation(verb),
            id_param=id_param,
            failure_key=f"{prefix}_{verb}_failed",
            start_options=verb == "start",
        )
        for verb, icon in icons.items()
    )


JOB_BUTTONS = _job_buttons("job", "job", "job_id")
COPY_JOB_BUTTONS = _job_buttons("copy_job", "copy_job", "id")
REPOSITORY_BUTTONS = (
    VeeamButtonDescription(
        key="rescan",
        translation_key="repository_rescan",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:sync",
        operation="backup_repository.backup_repository_start_synchronize_action",
        id_param="repository_id",
        failure_key="repository_rescan_failed",
        name_placeholder="repository_name",
    ),
)


def _active_maintenance_session(data: dict[str, Any], repo_id: str) -> str | None:
    session = (data.get("repository_maintenance") or {}).get(repo_id) or {}
    if session.get("status_raw") in ACTIVE_MAINTENANCE_STATUSES:
        return session.get("session_id")
    return None


# API v8. Starting waits up to an hour for the repository's running sessions to finish and
# is canceled if they do not, rather than force-stopping backups in progress.
REPOSITORY_MAINTENANCE_BUTTONS = (
    VeeamButtonDescription(
        key="start_maintenance",
        translation_key="repository_start_maintenance",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:wrench",
        operation="repository_maintenance_session.repository_maintenance_sessions_start_action",
        id_param=None,
        failure_key="repository_start_maintenance_failed",
        name_placeholder="repository_name",
        body_fn=lambda models, repo_id: models.RESTBackupRepositoryMaintenanceSessionStartRequest(
            repository_ids=[repo_id],
            waiting_config=models.RESTBackupRepositoryMaintenanceSessionWaitingConfig(
                wait_for_sessions_timeout=60, force_stop_sessions=False
            ),
        ),
    ),
    VeeamButtonDescription(
        key="stop_maintenance",
        translation_key="repository_stop_maintenance",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:wrench-check",
        operation="repository_maintenance_session.repository_maintenance_sessions_stop_action",
        id_param="session_id",
        failure_key="repository_stop_maintenance_failed",
        name_placeholder="repository_name",
        target_fn=_active_maintenance_session,
        nothing_message="the repository is not under maintenance",
    ),
)
ORGANIZATION_BUTTONS = (
    VeeamButtonDescription(
        key="synchronize",
        translation_key="organization_synchronize",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:account-sync",
        operation="organization_sync.organization_sync_start",
        id_param="organization_id",
        failure_key="organization_synchronize_failed",
        name_placeholder="organization_name",
        # An incremental sync, as the console's Synchronize does by default
        body_fn=lambda models, _item_id: models.RESTOrganizationSyncOptions(
            type_=models.RESTOrganizationSyncOptionsType.INCREMENTAL
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Veeam buttons."""
    coordinator = entry.runtime_data["coordinator"]
    sdk = coordinator.sdk

    def factory(key: str, descriptions: tuple[VeeamButtonDescription, ...]):
        # A version without an operation gets no button for it, rather than one that fails
        available = [d for d in descriptions if sdk.has_operation(d.operation)]

        def create(item: dict[str, Any]) -> list[VeeamButton]:
            return [VeeamButton(coordinator, entry, key, item, d) for d in available]

        return create

    async_track_items(coordinator, entry, "jobs", factory("jobs", JOB_BUTTONS), async_add_entities)
    async_track_items(
        coordinator, entry, "copy_jobs", factory("copy_jobs", COPY_JOB_BUTTONS), async_add_entities
    )
    async_track_items(
        coordinator,
        entry,
        "repositories",
        factory(
            "repositories",
            REPOSITORY_BUTTONS
            # VB365 8.6 and later; decided once, at setup
            + (
                REPOSITORY_MAINTENANCE_BUTTONS
                if supports_repository_maintenance(sdk, coordinator.server_version)
                else ()
            ),
        ),
        async_add_entities,
    )
    async_track_items(
        coordinator,
        entry,
        "organizations",
        factory("organizations", ORGANIZATION_BUTTONS),
        async_add_entities,
    )


class VeeamButton(VeeamItemEntity, ButtonEntity):
    """Start, stop, enable or disable a job, or synchronize a repository or organization."""

    entity_description: VeeamButtonDescription

    def __init__(self, coordinator, entry, key, item, description: VeeamButtonDescription):
        self.entity_description = description
        super().__init__(
            coordinator, entry, key, item, description.key, description.translation_key
        )

    def _failure(self, error: str) -> HomeAssistantError:
        description = self.entity_description
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=description.failure_key,
            translation_placeholders={
                description.name_placeholder: str(self.item_name or self.item_id),
                "error": error,
            },
        )

    async def async_press(self) -> None:
        """Call the operation, and raise if the server did not accept it."""
        description = self.entity_description
        sdk = self.coordinator.sdk
        kwargs: dict[str, Any] = {}
        if description.id_param is not None:
            target: str | None = self.item_id
            if description.target_fn is not None:
                target = description.target_fn(self.coordinator.data or {}, self.item_id)
                if target is None:
                    raise self._failure(description.nothing_message)
            kwargs[description.id_param] = target
        if description.start_options and sdk.accepts(description.operation, "body"):
            # An incremental run, as the Start button in the console does
            kwargs["body"] = sdk.models.RESTStartJobOptions(full=False)
        elif description.body_fn is not None:
            kwargs["body"] = description.body_fn(sdk.models, self.item_id)

        try:
            async with asyncio.timeout(ACTION_TIMEOUT):
                result = await self.coordinator.client.call(
                    sdk.operation(description.operation), **kwargs
                )
        except VeeamAuthenticationError as err:
            self._entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="authentication_failed",
                translation_placeholders={"username": str(self._entry.data.get(CONF_USERNAME, ""))},
            ) from err
        except TimeoutError as err:
            raise self._failure(f"no answer within {ACTION_TIMEOUT:.0f} seconds") from err
        except (VeeamError, httpx.HTTPError, OSError) as err:
            raise self._failure(describe_error(err)) from err

        # Documented errors come back as a result, not an exception
        if is_error_response(result):
            raise self._failure(error_message(result))

        _LOGGER.info("%s: %s accepted", self.item_name, description.operation)
        await self.coordinator.async_request_refresh()
