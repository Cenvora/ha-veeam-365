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

from collections.abc import Callable
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

from .coordinator import (
    HEALTH_OPERATION,
    supports_organization_sync,
    supports_proxy_pools,
    supports_repository_maintenance,
)
from .entity import (
    OrganizationSyncMixin,
    ProxyPoolMixin,
    RepositoryMaintenanceMixin,
    VeeamItemEntity,
    VeeamLicenseEntity,
    VeeamServerEntity,
    async_track_items,
)

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
class VeeamItemBinaryDescription(BinarySensorEntityDescription):
    """A flag of one item's device. ``key`` is the unique ID suffix — never change it."""

    value_key: str
    icon_on: str
    icon_off: str
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


# The unique ID suffixes are historical: "online" reported is_out_of_sync all along, so it
# keeps its ID (and history) under the name that says what it is.
REPOSITORY_BINARY_SENSORS: tuple[VeeamItemBinaryDescription, ...] = (
    VeeamItemBinaryDescription(
        key="online",
        translation_key="repository_cache_in_sync",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_cache_in_sync",
        icon_on="mdi:sync",
        icon_off="mdi:sync-alert",
    ),
    VeeamItemBinaryDescription(
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
    VeeamItemBinaryDescription(
        key="immutable",
        translation_key="repository_immutable",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_immutable",
        icon_on="mdi:lock",
        icon_off="mdi:lock-open",
    ),
    VeeamItemBinaryDescription(
        key="accessible",
        translation_key="repository_accessible",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_accessible",
        icon_on="mdi:folder-open",
        icon_off="mdi:folder-lock",
    ),
)

# Read from the repository's current or latest maintenance session (API v8). No device
# class: maintenance is deliberate, not a fault.
REPOSITORY_MAINTENANCE_BINARY_SENSORS: tuple[VeeamItemBinaryDescription, ...] = (
    VeeamItemBinaryDescription(
        key="maintenance",
        translation_key="repository_maintenance",
        value_key="is_active",
        icon_on="mdi:wrench",
        icon_off="mdi:wrench-check",
        attributes_fn=lambda session: {
            "raw_value": session.get("status_raw"),
            "session_id": session.get("session_id"),
            "start_time": session.get("start_time"),
        },
    ),
)

PROXY_BINARY_SENSORS: tuple[VeeamItemBinaryDescription, ...] = (
    VeeamItemBinaryDescription(
        key="online",
        translation_key="proxy_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_key="is_online",
        icon_on="mdi:server-network",
        icon_off="mdi:server-network-off",
        attributes_fn=lambda proxy: {
            "raw_value": proxy.get("status_raw"),
            "fqdn": proxy.get("fqdn"),
            "port": proxy.get("port"),
            "roles": proxy.get("roles") or [],
            "proxy_pool_id": proxy.get("proxy_pool_id"),
        },
    ),
)

# Read from the pool's proxies (API v8)
PROXY_POOL_BINARY_SENSORS: tuple[VeeamItemBinaryDescription, ...] = (
    VeeamItemBinaryDescription(
        key="online",
        translation_key="proxy_pool_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_key="is_online",
        icon_on="mdi:server-network",
        icon_off="mdi:server-network-off",
        attributes_fn=lambda pool: {
            "online_proxies": pool.get("online_count"),
            "proxies": pool.get("proxy_count"),
        },
    ),
    VeeamItemBinaryDescription(
        key="degraded",
        translation_key="proxy_pool_degraded",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_key="is_degraded",
        icon_on="mdi:server-network-off",
        icon_off="mdi:server-network",
        attributes_fn=lambda pool: {"offline_proxies": pool.get("offline_proxies") or []},
    ),
)

# No device class: an organization without backups yet is not a fault
ORGANIZATION_BINARY_SENSORS: tuple[VeeamItemBinaryDescription, ...] = (
    VeeamItemBinaryDescription(
        key="backed_up",
        translation_key="organization_backed_up",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="is_backed_up",
        icon_on="mdi:cloud-check",
        icon_off="mdi:cloud-outline",
    ),
)

# Read from the organization's sync state (API v7 and later)
ORGANIZATION_SYNC_BINARY_SENSORS: tuple[VeeamItemBinaryDescription, ...] = (
    VeeamItemBinaryDescription(
        key="sync",
        translation_key="organization_sync",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_key="has_sync_error",
        icon_on="mdi:sync-alert",
        icon_off="mdi:account-sync",
        attributes_fn=lambda sync: {
            "raw_value": sync.get("last_result"),
            "error": sync.get("error"),
            "last_sync": sync.get("last_sync"),
            "type": sync.get("last_sync_type"),
            "parts": sync.get("parts") or {},
        },
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

    def item_sensors(key: str, descriptions: tuple[VeeamItemBinaryDescription, ...]):
        def factory(item: dict[str, Any]) -> list[BinarySensorEntity]:
            return [
                VeeamItemBinarySensor(coordinator, entry, key, item, description)
                for description in descriptions
            ]

        return factory

    # Decided once, at setup: a server upgraded to 8.6 gets these on the next restart
    maintenance_supported = supports_repository_maintenance(
        coordinator.sdk, coordinator.server_version
    )

    def repository_sensors(item: dict[str, Any]) -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = [
            VeeamItemBinarySensor(coordinator, entry, "repositories", item, description)
            for description in REPOSITORY_BINARY_SENSORS
        ]
        if maintenance_supported:
            entities.extend(
                VeeamRepositoryMaintenanceBinarySensor(
                    coordinator, entry, "repositories", item, description
                )
                for description in REPOSITORY_MAINTENANCE_BINARY_SENSORS
            )
        return entities

    async_track_items(coordinator, entry, "repositories", repository_sensors, add)
    async_track_items(
        coordinator, entry, "proxies", item_sensors("proxies", PROXY_BINARY_SENSORS), add
    )
    if supports_proxy_pools(coordinator.sdk):

        def pool_sensors(item: dict[str, Any]) -> list[BinarySensorEntity]:
            return [
                VeeamProxyPoolBinarySensor(coordinator, entry, "proxy_pools", item, description)
                for description in PROXY_POOL_BINARY_SENSORS
            ]

        async_track_items(coordinator, entry, "proxy_pools", pool_sensors, add)

    sync_supported = supports_organization_sync(coordinator.sdk)

    def organization_sensors(item: dict[str, Any]) -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = [
            VeeamItemBinarySensor(coordinator, entry, "organizations", item, description)
            for description in ORGANIZATION_BINARY_SENSORS
        ]
        if sync_supported:
            entities.extend(
                VeeamOrganizationSyncBinarySensor(
                    coordinator, entry, "organizations", item, description
                )
                for description in ORGANIZATION_SYNC_BINARY_SENSORS
            )
        return entities

    async_track_items(coordinator, entry, "organizations", organization_sensors, add)

    server_sensors: list[BinarySensorEntity] = [
        VeeamServerHealthOkSensor(coordinator, entry),
        VeeamServerConnectedSensor(coordinator, entry),
        VeeamLicenseAutoUpdateSensor(coordinator, entry),
    ]
    # The server's health report exists from API v8 only
    if coordinator.sdk.has_operation(HEALTH_OPERATION):
        server_sensors.append(VeeamServiceHealthSensor(coordinator, entry))
    add(server_sensors)


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


class VeeamServiceHealthSensor(VeeamServerEntity, BinarySensorEntity):
    """On (Problem) when the server's own health report says Unhealthy.

    Unlike Health OK, which only says whether this integration's polls got answers, this is
    the server's verdict on its NATS server and PostgreSQL configuration database. Each check
    is an attribute, and ``problems`` lists the descriptions of the failing ones for use in a
    notification.
    """

    endpoint = "health"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "service_health", "service_health")

    @property
    def health(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("health")

    @property
    def available(self) -> bool:
        return super().available and self.health is not None

    @property
    def is_on(self) -> bool | None:
        health = self.health
        healthy = health.get("is_healthy") if health else None
        return None if healthy is None else not healthy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = self.health or {}
        checks = health.get("checks") or {}
        return {
            "raw_value": health.get("status_raw"),
            "checks": checks,
            "problems": [
                check.get("description") or name
                for name, check in checks.items()
                if check.get("status") != "Healthy"
            ],
        }

    @property
    def icon(self) -> str:
        return "mdi:server-remove" if self.is_on else "mdi:server-network"


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
# REPOSITORY AND PROXY BINARY SENSORS (device per item)
# ===========================


class VeeamItemBinarySensor(VeeamItemEntity, BinarySensorEntity):
    """One flag of an item. Unknown on API versions that do not report it."""

    entity_description: VeeamItemBinaryDescription

    def __init__(self, coordinator, entry, key, item, description: VeeamItemBinaryDescription):
        self.entity_description = description
        super().__init__(
            coordinator, entry, key, item, description.key, description.translation_key
        )

    def _source(self) -> dict[str, Any] | None:
        return self.item

    @property
    def is_on(self) -> bool | None:
        source = self._source()
        value = source.get(self.entity_description.value_key) if source else None
        return None if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attributes_fn = self.entity_description.attributes_fn
        source = self._source()
        return attributes_fn(source) if attributes_fn and source else None

    @property
    def icon(self) -> str:
        description = self.entity_description
        return description.icon_on if self.is_on else description.icon_off


class VeeamOrganizationSyncBinarySensor(OrganizationSyncMixin, VeeamItemBinarySensor):
    """Problem when the last cache sync of an organization failed."""


class VeeamRepositoryMaintenanceBinarySensor(RepositoryMaintenanceMixin, VeeamItemBinarySensor):
    """On while a maintenance session suspends operations on the repository."""


class VeeamProxyPoolBinarySensor(ProxyPoolMixin, VeeamItemBinarySensor):
    """Whether a proxy pool can process, or has proxies offline."""
