"""Support for Veeam Backup for Microsoft 365 buttons.

Every press either does what it says or raises HomeAssistantError, which the UI shows as an
error toast. Nothing is swallowed: a button that fails silently looks exactly like one that
worked.
"""

from __future__ import annotations

import asyncio
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
from .coordinator import describe_error, error_message, is_error_response
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
    # Name of the operation's ID argument, which differs between endpoints
    id_param: str
    # Exception translation key, and the placeholder it names the item with
    failure_key: str
    name_placeholder: str = "job_name"
    # Whether the operation starts a job and takes RESTStartJobOptions
    start_options: bool = False


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
        factory("repositories", REPOSITORY_BUTTONS),
        async_add_entities,
    )


class VeeamButton(VeeamItemEntity, ButtonEntity):
    """Start, stop, enable or disable a job, or synchronize a repository."""

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
        kwargs: dict[str, Any] = {description.id_param: self.item_id}
        if description.start_options and sdk.accepts(description.operation, "body"):
            # An incremental run, as the Start button in the console does
            kwargs["body"] = sdk.models.RESTStartJobOptions(full=False)

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
