"""Entity base classes and device grouping shared by every platform."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_name
from .coordinator import VeeamCoordinator, current_ids, fetch_succeeded, is_prunable

MANUFACTURER = "Veeam"

# Per collection: the device identifier prefix, the kind named in the device name, and the
# device model. The models are what the companion dashboard groups by, so they must not
# change; the identifier prefixes are what existing devices are keyed on.
ITEM_KINDS: dict[str, tuple[str, str, str]] = {
    "jobs": ("job", "Job", "Backup Job"),
    "copy_jobs": ("copy_job", "Copy Job", "Backup Copy Job"),
    "repositories": ("repository", "Repository", "Backup Repository"),
}


def server_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"server_{entry.entry_id}")},
        name=device_name("Server", str(entry.data.get(CONF_HOST, "")) or None),
        manufacturer=MANUFACTURER,
        model="Backup for Microsoft 365",
    )


def license_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"license_{entry.entry_id}")},
        name=device_name("License"),
        manufacturer=MANUFACTURER,
        model="License",
    )


def item_device_info(key: str, item_id: str, name: str | None) -> DeviceInfo:
    prefix, kind, model = ITEM_KINDS[key]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{prefix}_{item_id}")},
        name=device_name(kind, name),
        manufacturer=MANUFACTURER,
        model=model,
    )


class VeeamEntity(CoordinatorEntity[VeeamCoordinator]):
    """An entity fed by one endpoint of the coordinator.

    Unavailable while that endpoint's last fetch failed: the data it would show is stale,
    and saying so beats presenting the last value, or an empty one, as current.
    """

    _attr_has_entity_name = True

    # Coordinator data key of the endpoint this entity reads; empty for none in particular
    endpoint = ""

    def __init__(
        self,
        coordinator: VeeamCoordinator,
        entry: ConfigEntry,
        unique_suffix: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_translation_key = translation_key

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return not self.endpoint or fetch_succeeded(self.coordinator.data, self.endpoint)


class VeeamServerEntity(VeeamEntity):
    """An entity on the server device."""

    endpoint = "server_info"

    def __init__(self, coordinator, entry, unique_suffix, translation_key) -> None:
        super().__init__(coordinator, entry, unique_suffix, translation_key)
        self._attr_device_info = server_device_info(entry)

    @property
    def server_info(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("server_info")


class VeeamLicenseEntity(VeeamEntity):
    """An entity on the license device."""

    endpoint = "license_info"

    def __init__(self, coordinator, entry, unique_suffix, translation_key) -> None:
        super().__init__(coordinator, entry, unique_suffix, translation_key)
        self._attr_device_info = license_device_info(entry)

    @property
    def license_info(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("license_info")

    @property
    def available(self) -> bool:
        return super().available and self.license_info is not None


class VeeamItemEntity(VeeamEntity):
    """An entity on the device of one job, copy job or repository."""

    def __init__(
        self,
        coordinator: VeeamCoordinator,
        entry: ConfigEntry,
        key: str,
        item: dict[str, Any],
        suffix: str,
        translation_key: str,
    ) -> None:
        prefix = ITEM_KINDS[key][0]
        self.endpoint = key
        self.item_id = str(item["id"])
        self.item_name = item.get("name")
        super().__init__(coordinator, entry, f"{prefix}_{self.item_id}_{suffix}", translation_key)
        self._attr_device_info = item_device_info(key, self.item_id, self.item_name)

    @property
    def item(self) -> dict[str, Any] | None:
        for candidate in (self.coordinator.data or {}).get(self.endpoint) or []:
            if str(candidate.get("id")) == self.item_id:
                return candidate
        return None

    @property
    def available(self) -> bool:
        return super().available and self.item is not None


def async_track_items(
    coordinator: VeeamCoordinator,
    entry: ConfigEntry,
    key: str,
    factory: Callable[[dict[str, Any]], Iterable[Entity]],
    async_add_entities: Callable[[list[Any]], None],
) -> None:
    """Add entities for every item of a collection, now and as new ones appear.

    An item pruned as stale (see __init__.async_prune_stale_devices) is forgotten here too,
    so it gets entities again if it ever comes back.
    """
    added: set[str] = set()

    @callback
    def _sync() -> None:
        data = coordinator.data
        if not data:
            return

        if is_prunable(data, key):
            added.intersection_update(current_ids(data, key))

        new_entities: list[Entity] = []
        for item in data.get(key) or []:
            item_id = str(item.get("id") or "")
            if not item_id or item_id in added:
                continue
            new_entities.extend(factory(item))
            added.add(item_id)

        if new_entities:
            async_add_entities(new_entities)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
