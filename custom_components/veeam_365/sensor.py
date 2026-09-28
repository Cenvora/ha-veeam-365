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
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import (
    PROTECTED_OPERATIONS,
    VeeamProtectedCountsCoordinator,
    reports_sync_progress,
    supports_job_sessions,
    supports_organization_sync,
    supports_proxy_pools,
    supports_repository_maintenance,
)
from .entity import (
    JobSessionMixin,
    OrganizationSyncMixin,
    ProxyPoolMixin,
    RepositoryMaintenanceMixin,
    VeeamItemEntity,
    VeeamLicenseEntity,
    VeeamServerEntity,
    async_track_items,
    item_device_info,
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


def _session_attributes(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "raw_value": session.get("status_raw"),
        "type": session.get("type"),
        "end_time": session.get("end_time"),
        "details": session.get("details"),
        "retry_count": session.get("retry_count"),
        "will_retry": session.get("will_retry"),
        "bottleneck": session.get("bottleneck"),
    }


# Read from the job's latest session (API v8); shared by backup jobs and copy jobs
JOB_SESSION_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="last_session",
        translation_key="job_last_session",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:history",
        value_fn=lambda session: session.get("creation_time"),
        attributes_fn=_session_attributes,
    ),
    VeeamSensorDescription(
        key="last_session_duration",
        translation_key="job_last_session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        icon="mdi:timer-outline",
        value_fn=lambda session: session.get("duration_seconds"),
    ),
    VeeamSensorDescription(
        key="last_session_transferred",
        translation_key="job_last_session_transferred",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        suggested_display_precision=1,
        icon="mdi:transfer",
        value_fn=lambda session: session.get("transferred_bytes"),
    ),
    VeeamSensorDescription(
        key="last_session_processed_objects",
        translation_key="job_last_session_processed_objects",
        icon="mdi:format-list-checks",
        value_fn=lambda session: session.get("processed_objects"),
    ),
    VeeamSensorDescription(
        key="last_session_processing_rate",
        translation_key="job_last_session_processing_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEBIBYTES_PER_SECOND,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:speedometer",
        value_fn=lambda session: session.get("processing_rate_bytes_per_second"),
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


def _repository_maintenance_icon(session: dict[str, Any]) -> str:
    if session.get("is_active"):
        return "mdi:wrench"
    if str(session.get("status_raw") or "").lower() == "failed":
        return "mdi:wrench-clock-outline"
    return "mdi:wrench-check"


# Read from the repository's current or latest maintenance session (API v8)
REPOSITORY_MAINTENANCE_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="maintenance_status",
        translation_key="repository_maintenance_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda session: session.get("status"),
        raw_key="status_raw",
        icon_fn=_repository_maintenance_icon,
        attributes_fn=lambda session: {
            "session_id": session.get("session_id"),
            "start_time": session.get("start_time"),
            "end_time": session.get("end_time"),
            "error": session.get("error"),
        },
    ),
)


def _maintenance_icon(proxy: dict[str, Any]) -> str:
    return {
        "enabled": "mdi:wrench",
        "enabling": "mdi:wrench-clock",
    }.get(str(proxy.get("maintenance_mode_raw") or "").lower(), "mdi:wrench-check")


def _reports_details(proxy: dict[str, Any]) -> bool:
    # Maintenance mode, usage, version and operating system: API v8 only
    return bool(proxy.get("reports_details"))


PROXY_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="maintenance_mode",
        translation_key="proxy_maintenance_mode",
        value_fn=lambda proxy: proxy.get("maintenance_mode"),
        raw_key="maintenance_mode_raw",
        icon_fn=_maintenance_icon,
        exists_fn=_reports_details,
    ),
    VeeamSensorDescription(
        key="cpu_usage",
        translation_key="proxy_cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:cpu-64-bit",
        value_fn=lambda proxy: proxy.get("cpu_usage_percent"),
        exists_fn=_reports_details,
    ),
    VeeamSensorDescription(
        key="memory_usage",
        translation_key="proxy_memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:memory",
        value_fn=lambda proxy: proxy.get("memory_usage_percent"),
        exists_fn=_reports_details,
    ),
    VeeamSensorDescription(
        key="version",
        translation_key="proxy_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:tag",
        value_fn=lambda proxy: proxy.get("version"),
        exists_fn=_reports_details,
    ),
    VeeamSensorDescription(
        key="operating_system",
        translation_key="proxy_operating_system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda proxy: proxy.get("operating_system"),
        raw_key="operating_system_raw",
        icon_fn=lambda proxy: (
            "mdi:linux"
            if str(proxy.get("operating_system_raw") or "").lower() == "linux"
            else "mdi:microsoft-windows"
        ),
        exists_fn=_reports_details,
    ),
)

# Read from the pool's proxies (API v8)
PROXY_POOL_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="proxies",
        translation_key="proxy_pool_proxies",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:server",
        value_fn=lambda pool: pool.get("proxy_count"),
        attributes_fn=lambda pool: {
            "description": pool.get("description"),
            "proxies": pool.get("proxies") or [],
            "repositories": pool.get("repositories") or [],
        },
    ),
    VeeamSensorDescription(
        key="online_proxies",
        translation_key="proxy_pool_online_proxies",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:server-network",
        value_fn=lambda pool: pool.get("online_count"),
    ),
)

ORGANIZATION_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="last_backup",
        translation_key="organization_last_backup",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:backup-restore",
        value_fn=lambda org: org.get("last_backup"),
        attributes_fn=lambda org: {
            "first_backup": org.get("first_backup"),
            "office_name": org.get("office_name"),
            "services": org.get("services") or [],
        },
    ),
    VeeamSensorDescription(
        key="licensed_users",
        translation_key="organization_licensed_users",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-multiple-check",
        value_fn=lambda org: org.get("licensed_users"),
    ),
    VeeamSensorDescription(
        key="new_users",
        translation_key="organization_new_users",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-multiple-plus",
        value_fn=lambda org: org.get("new_users"),
    ),
    VeeamSensorDescription(
        key="type",
        translation_key="organization_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:domain",
        value_fn=lambda org: org.get("type"),
        raw_key="type_raw",
    ),
    VeeamSensorDescription(
        key="region",
        translation_key="organization_region",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:earth",
        value_fn=lambda org: org.get("region"),
        raw_key="region_raw",
    ),
)

# Read from the organization's sync state (API v7 and later)
ORGANIZATION_SYNC_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="last_sync",
        translation_key="organization_last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:account-sync",
        value_fn=lambda sync: sync.get("last_sync"),
        attributes_fn=lambda sync: {
            "result": sync.get("last_result"),
            "type": sync.get("last_sync_type"),
        },
    ),
)

# Only v8 reports the sync in progress or queued
ORGANIZATION_SYNC_PROGRESS_SENSORS: tuple[VeeamSensorDescription, ...] = (
    VeeamSensorDescription(
        key="sync_status",
        translation_key="organization_sync_status",
        value_fn=lambda sync: sync.get("current_status"),
        raw_key="current_status_raw",
        icon_fn=lambda sync: {
            "running": "mdi:sync",
            "queued": "mdi:timer-sand",
        }.get(str(sync.get("current_status_raw") or "").lower(), "mdi:sync-off"),
        attributes_fn=lambda sync: {"next_sync": sync.get("next_sync")},
    ),
)

# Icons for the protected object counts, by kind (see PROTECTED_OPERATIONS)
PROTECTED_ICONS = {
    "users": "mdi:account-lock",
    "groups": "mdi:account-group",
    "sites": "mdi:web",
    "teams": "mdi:microsoft-teams",
}

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

    sessions_supported = supports_job_sessions(coordinator.sdk)

    def _job_sensors(key: str, descriptions: tuple[VeeamSensorDescription, ...]):
        item_factory = _item_sensors(key, descriptions)

        def factory(item: dict[str, Any]) -> list[SensorEntity]:
            entities: list[SensorEntity] = list(item_factory(item))
            if sessions_supported:
                entities.extend(
                    VeeamJobSessionSensor(coordinator, entry, key, item, description)
                    for description in JOB_SESSION_SENSORS
                )
            return entities

        return factory

    async_track_items(
        coordinator, entry, "jobs", _job_sensors("jobs", JOB_SENSORS), async_add_entities
    )
    async_track_items(
        coordinator,
        entry,
        "copy_jobs",
        _job_sensors("copy_jobs", COPY_JOB_SENSORS),
        async_add_entities,
    )
    # Decided once, at setup: a server upgraded to 8.6 gets these on the next restart
    maintenance_supported = supports_repository_maintenance(
        coordinator.sdk, coordinator.server_version
    )

    def repository_sensors(item: dict[str, Any]) -> list[SensorEntity]:
        entities: list[SensorEntity] = [
            VeeamItemSensor(coordinator, entry, "repositories", item, description)
            for description in REPOSITORY_SENSORS
            if description.exists_fn(item)
        ]
        if maintenance_supported:
            entities.extend(
                VeeamRepositoryMaintenanceSensor(
                    coordinator, entry, "repositories", item, description
                )
                for description in REPOSITORY_MAINTENANCE_SENSORS
            )
        return entities

    async_track_items(coordinator, entry, "repositories", repository_sensors, async_add_entities)
    async_track_items(
        coordinator, entry, "proxies", _item_sensors("proxies", PROXY_SENSORS), async_add_entities
    )
    if supports_proxy_pools(coordinator.sdk):

        def pool_sensors(item: dict[str, Any]) -> list[SensorEntity]:
            return [
                VeeamProxyPoolSensor(coordinator, entry, "proxy_pools", item, description)
                for description in PROXY_POOL_SENSORS
            ]

        async_track_items(coordinator, entry, "proxy_pools", pool_sensors, async_add_entities)

    sync_descriptions: tuple[VeeamSensorDescription, ...] = ()
    if supports_organization_sync(coordinator.sdk):
        sync_descriptions += ORGANIZATION_SYNC_SENSORS
    if reports_sync_progress(coordinator.sdk):
        sync_descriptions += ORGANIZATION_SYNC_PROGRESS_SENSORS

    protected_counts: VeeamProtectedCountsCoordinator | None = entry.runtime_data.get(
        "protected_counts"
    )

    def organization_sensors(item: dict[str, Any]) -> list[SensorEntity]:
        entities: list[SensorEntity] = [
            VeeamItemSensor(coordinator, entry, "organizations", item, description)
            for description in ORGANIZATION_SENSORS
        ]
        entities.extend(
            VeeamOrganizationSyncSensor(coordinator, entry, "organizations", item, description)
            for description in sync_descriptions
        )
        if protected_counts is not None:
            entities.extend(
                VeeamProtectedCountSensor(protected_counts, entry, item, kind)
                for kind in PROTECTED_OPERATIONS
            )
        return entities

    async_track_items(coordinator, entry, "organizations", organization_sensors, async_add_entities)

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
    """A sensor on a job, copy job, repository, proxy, pool or organization device."""

    def __init__(self, coordinator, entry, key, item, description: VeeamSensorDescription):
        self.entity_description = description
        super().__init__(
            coordinator, entry, key, item, description.key, description.translation_key
        )

    def _source(self) -> dict[str, Any] | None:
        return self.item


class VeeamOrganizationSyncSensor(OrganizationSyncMixin, VeeamItemSensor):
    """A sensor on an organization device showing its cache sync state."""


class VeeamJobSessionSensor(JobSessionMixin, VeeamItemSensor):
    """A sensor on a job or copy job device showing its latest session."""


class VeeamRepositoryMaintenanceSensor(RepositoryMaintenanceMixin, VeeamItemSensor):
    """A sensor on a repository device showing its maintenance session."""


class VeeamProxyPoolSensor(ProxyPoolMixin, VeeamItemSensor):
    """A sensor on a proxy pool device, counting its proxies."""


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


class VeeamProtectedCountSensor(CoordinatorEntity[VeeamProtectedCountsCoordinator], SensorEntity):
    """How many users, groups, sites or teams an organization protects (API v8).

    Fed by the hourly counts coordinator rather than the regular poll, but on the
    organization's device, so pruning the organization removes it too. Unavailable until the
    first count is in, and while its kind could not be counted.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: VeeamProtectedCountsCoordinator,
        entry: ConfigEntry,
        item: dict[str, Any],
        kind: str,
    ) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._org_id = str(item["id"])
        self._attr_unique_id = f"{entry.entry_id}_organization_{self._org_id}_protected_{kind}"
        self._attr_translation_key = f"organization_protected_{kind}"
        self._attr_icon = PROTECTED_ICONS[kind]
        self._attr_device_info = item_device_info("organizations", self._org_id, item.get("name"))

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        if not super().available or not data:
            return False
        return bool((data.get("fetch_ok") or {}).get(self._kind))

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if not data:
            return None
        # Absent means none of this kind, once the kind was counted
        return ((data.get("counts") or {}).get(self._org_id) or {}).get(self._kind, 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {"counted_at": data.get("counted_at")}
