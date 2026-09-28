"""Binary sensors for Veeam Backup for Microsoft 365.

These live in their own platform rather than alongside the sensors on purpose. Home Assistant
derives an entity domain from the platform that creates it, so a BinarySensorEntity added by the
sensor platform lands in the sensor domain — where the binary-sensor device class wording never
applies and every state displays as a raw "on"/"off". In the binary_sensor domain the same
entities read as Connected/Disconnected, OK/Problem and Running/Not running.

Entities that existed under the sensor domain are removed as their replacements are created, so
one upgrade moves them rather than leaving two of everything.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VeeamItemEntity, VeeamLicenseEntity, VeeamServerEntity, async_track_items

_LOGGER = logging.getLogger(__name__)

# Coordinator-driven: nothing is polled per entity
PARALLEL_UPDATES = 0


def _drop_superseded_sensor_entities(hass: HomeAssistant, entry: ConfigEntry, entities) -> None:
    """Remove the sensor-domain entities these binary sensors replace.

    Matching is by unique ID, which is unchanged — only the domain moves. Without this an
    upgrade would leave the old sensor.* entities behind as unavailable strays, since nothing
    provides them any more.
    """
    registry = er.async_get(hass)
    unique_ids = {entity.unique_id for entity in entities if entity.unique_id}

    for existing in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if existing.domain != "sensor" or existing.unique_id not in unique_ids:
            continue
        _LOGGER.info(
            "Replacing %s with its binary_sensor equivalent; update any automation or "
            "dashboard that referenced it",
            existing.entity_id,
        )
        registry.async_remove(existing.entity_id)


@dataclass(frozen=True, kw_only=True)
class VeeamRepositoryBinaryDescription(BinarySensorEntityDescription):
    """A repository flag. ``key`` is the unique ID suffix — never change it."""

    value_key: str
    icon_on: str
    icon_off: str


# The unique ID suffixes are historical: "online" reported is_out_of_sync all along, so it
# keeps its ID (and history) under the name that says what it is.
REPOSITORY_BINARY_SENSORS: tuple[VeeamRepositoryBinaryDescription, ...] = (
    VeeamRepositoryBinaryDescription(
        key="online",
        translation_key="repository_cache_in_sync",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_cache_in_sync",
        icon_on="mdi:sync",
        icon_off="mdi:sync-alert",
    ),
    VeeamRepositoryBinaryDescription(
        key="out_of_date",
        translation_key="repository_out_of_date",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_outdated",
        icon_on="mdi:alert-octagon",
        icon_off="mdi:check-decagram",
    ),
    # No device class: immutability being off is a configuration choice, not a problem,
    # and PROBLEM would colour it red
    VeeamRepositoryBinaryDescription(
        key="immutable",
        translation_key="repository_immutable",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_immutable",
        icon_on="mdi:lock",
        icon_off="mdi:lock-open",
    ),
    VeeamRepositoryBinaryDescription(
        key="accessible",
        translation_key="repository_accessible",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_accessible",
        icon_on="mdi:folder-open",
        icon_off="mdi:folder-lock",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Veeam binary sensors."""
    coordinator = entry.runtime_data["coordinator"]

    def add(entities: list[BinarySensorEntity]) -> None:
        _drop_superseded_sensor_entities(hass, entry, entities)
        async_add_entities(entities)

    def repository_sensors(item: dict[str, Any]) -> list[BinarySensorEntity]:
        return [
            VeeamRepositoryBinarySensor(coordinator, entry, item, description)
            for description in REPOSITORY_BINARY_SENSORS
        ]

    async_track_items(coordinator, entry, "repositories", repository_sensors, add)

    add(
        [
            VeeamServerHealthOkSensor(coordinator, entry),
            VeeamServerConnectedSensor(coordinator, entry),
            VeeamLicenseAutoUpdateSensor(coordinator, entry),
        ]
    )


# ===========================
# SERVER BINARY SENSORS (single device)
# ===========================


class _ServerStatusSensor(VeeamServerEntity, BinarySensorEntity):
    """A sensor about the connection itself.

    Always available: "unavailable" is what these report *about*, so they have to be able
    to say off rather than disappear exactly when they matter.
    """

    endpoint = ""
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True


class VeeamServerHealthOkSensor(_ServerStatusSensor):
    """On when the last poll succeeded and every endpoint answered."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "server_health_ok", "server_health_ok")

    @property
    def is_on(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        diagnostics = (self.coordinator.data or {}).get("diagnostics") or {}
        return bool(diagnostics.get("health_ok"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        diagnostics = (self.coordinator.data or {}).get("diagnostics") or {}
        return {"failed_endpoints": diagnostics.get("failed_endpoints") or []}

    @property
    def icon(self) -> str:
        return "mdi:heart-pulse" if self.is_on else "mdi:heart-off"


class VeeamServerConnectedSensor(_ServerStatusSensor):
    """On while polls reach the server and it answers."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "server_connected", "server_connected")

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def icon(self) -> str:
        return "mdi:lan-connect" if self.is_on else "mdi:lan-disconnect"


# ===========================
# LICENSE BINARY SENSORS (single device)
# ===========================


class VeeamLicenseAutoUpdateSensor(VeeamLicenseEntity, BinarySensorEntity):
    """Binary sensor for Veeam License Auto Update."""

    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:update"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "license_auto_update", "license_auto_update")

    @property
    def is_on(self) -> bool | None:
        license_info = self.license_info
        return license_info.get("auto_update_enabled") if license_info else None


# ===========================
# REPOSITORY BINARY SENSORS (device per repository)
# ===========================


class VeeamRepositoryBinarySensor(VeeamItemEntity, BinarySensorEntity):
    """One repository flag. Unknown on API versions that do not report it."""

    entity_description: VeeamRepositoryBinaryDescription

    def __init__(self, coordinator, entry, item, description: VeeamRepositoryBinaryDescription):
        self.entity_description = description
        super().__init__(
            coordinator, entry, "repositories", item, description.key, description.translation_key
        )

    @property
    def is_on(self) -> bool | None:
        repo = self.item
        value = repo.get(self.entity_description.value_key) if repo else None
        return None if value is None else bool(value)

    @property
    def icon(self) -> str:
        description = self.entity_description
        return description.icon_on if self.is_on else description.icon_off
