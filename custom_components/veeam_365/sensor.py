"""Support for Veeam Backup for Microsoft 365 sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import (
    VeeamItemEntity,
    VeeamLicenseEntity,
    VeeamServerEntity,
    async_track_items,
)

# Coordinator-driven: nothing is polled per entity
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VeeamSensorDescription(SensorEntityDescription):
    """A sensor reading one value out of a coordinator dictionary.

    ``key`` is the unique ID suffix, which existing entities are keyed on — never change it.
    """

    value_fn: Callable[[dict[str, Any]], Any]
    raw_key: str | None = None
    icon_fn: Callable[[dict[str, Any]], str] | None = None
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True


def _yes_no(value: Any) -> str | None:
    if value is None:
        return None
    return "Yes" if value else "No"


def _enabled_icon(item: dict[str, Any]) -> str:
    return "mdi:play-circle" if item.get("is_enabled") else "mdi:pause-circle"


def _status_icon(item: dict[str, Any]) -> str:
    status = str(item.get("last_status_raw") or item.get("last_status") or "").lower()
    return {
        "success": "mdi:check-circle",
        "warning": "mdi:alert",
        "failed": "mdi:close-circle",
        "running": "mdi:play",
    }.get(status, "mdi:cloud-sync")


def _repository_icon(repo: dict[str, Any]) -> str:
    # Matched on the raw API value, so a reworded label cannot change which icon shows
    repo_type = str(repo.get("type_raw") or repo.get("type") or "").lower()
    if "linux" in repo_type:
        return "mdi:linux"
    if "win" in repo_type:
        return "mdi:microsoft-windows"
    if any(word in repo_type for word in ("cloud", "azure", "aws", "s3", "wasabi", "object")):
        return "mdi:cloud"
    return "mdi:database"


JOB_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="name",
        translation_key="job_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:label",
        value_fn=lambda job: job.get("name"),
    ),
    VeeamSensorDescription(
        key="backup_type",
        translation_key="job_backup_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:file-tree",
        value_fn=lambda job: job.get("backup_type"),
        raw_key="backup_type_raw",
    ),
    VeeamSensorDescription(
        key="last_run",
        translation_key="job_last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-start",
        value_fn=lambda job: job.get("last_run"),
    ),
    VeeamSensorDescription(
        key="next_run",
        translation_key="job_next_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-end",
        value_fn=lambda job: job.get("next_run"),
    ),
    VeeamSensorDescription(
        key="last_backup",
        translation_key="job_last_backup",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:backup-restore",
        value_fn=lambda job: job.get("last_backup"),
    ),
    VeeamSensorDescription(
        key="is_enabled",
        translation_key="job_is_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda job: _yes_no(job.get("is_enabled")),
        icon_fn=_enabled_icon,
    ),
    VeeamSensorDescription(
        key="last_status",
        translation_key="job_last_status",
        value_fn=lambda job: job.get("last_status"),
        raw_key="last_status_raw",
        icon_fn=_status_icon,
    ),
)

COPY_JOB_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="name",
        translation_key="copy_job_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:label",
        value_fn=lambda job: job.get("name"),
    ),
    VeeamSensorDescription(
        key="last_run",
        translation_key="copy_job_last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-start",
        value_fn=lambda job: job.get("last_run"),
    ),
    VeeamSensorDescription(
        key="last_backup",
        translation_key="copy_job_last_backup",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:backup-restore",
        value_fn=lambda job: job.get("last_backup"),
    ),
    VeeamSensorDescription(
        key="is_enabled",
        translation_key="copy_job_is_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda job: _yes_no(job.get("is_enabled")),
        icon_fn=_enabled_icon,
    ),
    VeeamSensorDescription(
        key="last_status",
        translation_key="copy_job_last_status",
        value_fn=lambda job: job.get("last_status"),
        raw_key="last_status_raw",
        icon_fn=_status_icon,
    ),
)

REPOSITORY_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="type",
        translation_key="repository_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda repo: repo.get("type"),
        raw_key="type_raw",
        icon_fn=_repository_icon,
    ),
    VeeamSensorDescription(
        key="description",
        translation_key="repository_description",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:text",
        value_fn=lambda repo: repo.get("description"),
    ),
    VeeamSensorDescription(
        key="used_space",
        translation_key="repository_used_space",
        # Computed in binary units, so labelled as such. It used to say "GB" while
        # dividing by 1024³.
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:database-alert",
        value_fn=lambda repo: repo.get("used_space_gib"),
        attributes_fn=lambda repo: {
            "used_space_bytes": repo.get("used_space_bytes"),
            "capacity_bytes": repo.get("capacity_bytes"),
            "free_space_bytes": repo.get("free_space_bytes"),
        },
    ),
    VeeamSensorDescription(
        key="immutability_days",
        translation_key="repository_immutability_days",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.DAYS,
        icon="mdi:calendar-lock",
        value_fn=lambda repo: repo.get("immutability_days"),
        # Only meaningful when immutability is on and has a period
        exists_fn=lambda repo: bool(repo.get("is_immutable"))
        and repo.get("immutability_days") is not None,
    ),
)

SERVER_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="server_version",
        translation_key="server_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:tag",
        value_fn=lambda info: info.get("version"),
    ),
    VeeamSensorDescription(
        key="server_installation_id",
        translation_key="server_installation_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:identifier",
        value_fn=lambda info: info.get("installation_id"),
    ),
)

LICENSE_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="license_status",
        translation_key="license_status",
        value_fn=lambda lic: lic.get("status"),
        raw_key="status_raw",
        icon_fn=lambda lic: (
            "mdi:license-off"
            if str(lic.get("status_raw") or "").lower() == "expired"
            else "mdi:license"
        ),
    ),
    VeeamSensorDescription(
        key="license_type",
        translation_key="license_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:file-document",
        value_fn=lambda lic: lic.get("type"),
        raw_key="type_raw",
    ),
    VeeamSensorDescription(
        key="license_expiration",
        translation_key="license_expiration",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-end",
        value_fn=lambda lic: lic.get("expiration_date"),
    ),
    VeeamSensorDescription(
        key="license_grace_period_expires",
        translation_key="license_grace_period_expires",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
        value_fn=lambda lic: lic.get("grace_period_expires"),
    ),
    VeeamSensorDescription(
        key="license_licensed_to",
        translation_key="license_licensed_to",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:account",
        value_fn=lambda lic: lic.get("licensed_to"),
    ),
    VeeamSensorDescription(
        key="license_total_number",
        translation_key="license_total_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
        value_fn=lambda lic: lic.get("total_number"),
    ),
    VeeamSensorDescription(
        key="license_used_number",
        translation_key="license_used_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-multiple-check",
        value_fn=lambda lic: lic.get("used_number"),
    ),
    VeeamSensorDescription(
        key="license_new_number",
        translation_key="license_new_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-multiple-plus",
        value_fn=lambda lic: lic.get("new_number"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Veeam sensors."""
    coordinator = entry.runtime_data["coordinator"]

    def _item_sensors(key: str, descriptions: tuple[VeeamSensorDescription, ...]):
        def factory(item: dict[str, Any]) -> list[VeeamItemSensor]:
            return [
                VeeamItemSensor(coordinator, entry, key, item, description)
                for description in descriptions
                if description.exists_fn(item)
            ]

        return factory

    async_track_items(
        coordinator, entry, "jobs", _item_sensors("jobs", JOB_SENSORS), async_add_entities
    )
    async_track_items(
        coordinator,
        entry,
        "copy_jobs",
        _item_sensors("copy_jobs", COPY_JOB_SENSORS),
        async_add_entities,
    )
    async_track_items(
        coordinator,
        entry,
        "repositories",
        _item_sensors("repositories", REPOSITORY_SENSORS),
        async_add_entities,
    )

    # One server and one license per entry. The license device is created even if the
    # license could not be read on the first poll: its entities are simply unavailable until
    # it can, rather than never appearing.
    singletons: list[SensorEntity] = [
        VeeamServerSensor(coordinator, entry, description) for description in SERVER_SENSORS
    ]
    singletons.append(VeeamLastSuccessfulPollSensor(coordinator, entry))
    singletons.extend(
        VeeamLicenseSensor(coordinator, entry, description) for description in LICENSE_SENSORS
    )
    async_add_entities(singletons)


class _DescribedSensor(SensorEntity):
    """Shared native_value/icon/attributes for a description-driven sensor."""

    entity_description: VeeamSensorDescription

    def _source(self) -> dict[str, Any] | None:
        raise NotImplementedError

    @property
    def native_value(self) -> Any:
        source = self._source()
        return self.entity_description.value_fn(source) if source else None

    @property
    def icon(self) -> str | None:
        source = self._source()
        if source and self.entity_description.icon_fn:
            return self.entity_description.icon_fn(source)
        return self.entity_description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        description = self.entity_description
        if description.raw_key is None and description.attributes_fn is None:
            return None
        source = self._source() or {}
        attributes: dict[str, Any] = {}
        if description.raw_key is not None:
            # The unprettified API value, for automations that match exactly
            attributes["raw_value"] = source.get(description.raw_key)
        if description.attributes_fn is not None and source:
            attributes.update(description.attributes_fn(source))
        return attributes


class VeeamItemSensor(VeeamItemEntity, _DescribedSensor):
    """A sensor on a job, copy job or repository device."""

    def __init__(self, coordinator, entry, key, item, description: VeeamSensorDescription):
        self.entity_description = description
        super().__init__(
            coordinator, entry, key, item, description.key, description.translation_key
        )

    def _source(self) -> dict[str, Any] | None:
        return self.item


class VeeamServerSensor(VeeamServerEntity, _DescribedSensor):
    """A sensor on the server device."""

    def __init__(self, coordinator, entry, description: VeeamSensorDescription):
        self.entity_description = description
        super().__init__(coordinator, entry, description.key, description.translation_key)

    def _source(self) -> dict[str, Any] | None:
        return self.server_info


class VeeamLicenseSensor(VeeamLicenseEntity, _DescribedSensor):
    """A sensor on the license device."""

    def __init__(self, coordinator, entry, description: VeeamSensorDescription):
        self.entity_description = description
        super().__init__(coordinator, entry, description.key, description.translation_key)

    def _source(self) -> dict[str, Any] | None:
        return self.license_info


class VeeamLastSuccessfulPollSensor(VeeamServerEntity, SensorEntity):
    """When the server last answered a poll.

    Stays available while polls fail: how long ago the last good one was is exactly what
    is worth knowing then.
    """

    endpoint = ""
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "server_last_successful_poll", "server_last_successful_poll"
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        diagnostics = (self.coordinator.data or {}).get("diagnostics") or {}
        return diagnostics.get("last_successful_poll")
