"""The Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
import httpx
from veeam_365.exceptions import VeeamAuthenticationError, VeeamError

from .api_version import async_resolve_api_version
from .const import (
    API_VERSIONS,
    AUTO_API_VERSION,
    CONF_API_VERSION,
    CONNECT_TIMEOUT,
    DEFAULT_API_MODULE,
    DEFAULT_API_VERSION,
    DOMAIN,
)
from .coordinator import (
    VeeamCoordinator,
    current_ids,
    describe_error,
    is_prunable,
    license_issue_id,
)
from .sdk import create_client, load_sdk

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


# Device identifier prefixes, mapped to the coordinator data that keeps them alive
DEVICE_KINDS = {
    "job_": "jobs",
    "copy_job_": "copy_jobs",
    "repository_": "repositories",
}
SINGLETON_KINDS = {
    "server_": "server_info",
    "license_": "license_info",
}


def _item_kind(identifier: str) -> tuple[str, str] | None:
    """Split a device identifier into its DEVICE_KINDS prefix and item ID.

    Longest prefix first: "copy_job_" starts with neither "job_" nor "repository_", but a
    future prefix that nests would otherwise match the shorter one.
    """
    for prefix in sorted(DEVICE_KINDS, key=len, reverse=True):
        if identifier.startswith(prefix):
            return prefix, identifier[len(prefix) :]
    return None


def device_is_current(identifiers, data: dict | None, entry_id: str) -> bool:
    """Whether a device still corresponds to something the server reports.

    Used to decide whether Home Assistant should let the user delete a device. A device that
    is still being reported would simply reappear on the next poll, so refusing is kinder than
    letting someone delete it twice.

    An identifier this version does not recognise counts as stale: it cannot be something the
    current code maintains.
    """
    if not data:
        # Nothing to compare against — let the user clean up rather than blocking them
        return False

    for domain, identifier in identifiers:
        if domain != DOMAIN:
            continue

        match = _item_kind(identifier)
        if match is not None:
            prefix, wanted = match
            # IDs are compared as strings: the SDK parses them as uuid.UUID
            return any(
                str(item.get("id")) == wanted for item in data.get(DEVICE_KINDS[prefix]) or []
            )

        for prefix, key in SINGLETON_KINDS.items():
            if identifier.startswith(prefix):
                # These are one per config entry, so the suffix is the entry id
                return identifier == f"{prefix}{entry_id}" and data.get(key) is not None

    return False


@callback
def async_prune_stale_devices(hass: HomeAssistant, entry: ConfigEntry, data: dict | None) -> None:
    """Detach devices, and their entities, for objects the server no longer reports.

    The one place pruning happens, for every platform. A collection whose fetch failed this
    cycle, or that came back empty, is never pruned from — either would look like every
    object being deleted (see coordinator.is_prunable). Deleting the last object of a kind
    is left to the device's Delete button.

    Only this entry's devices are looked at, and only its claim on them is removed, so a
    second server reporting an object with the same ID keeps its device.
    """
    if not data:
        return

    present = {key: current_ids(data, key) for key in DEVICE_KINDS.values()}
    prunable = {key: is_prunable(data, key) for key in DEVICE_KINDS.values()}

    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    for device in list(dr.async_entries_for_config_entry(device_reg, entry.entry_id)):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            match = _item_kind(identifier)
            if match is None:
                continue
            prefix, item_id = match
            key = DEVICE_KINDS[prefix]
            if not prunable[key] or item_id in present[key]:
                continue

            _LOGGER.info(
                "Removing %s: the server no longer reports %s %s",
                device.name,
                prefix.rstrip("_").replace("_", " "),
                item_id,
            )
            for entity in er.async_entries_for_device(
                entity_reg, device.id, include_disabled_entities=True
            ):
                if entity.config_entry_id == entry.entry_id:
                    entity_reg.async_remove(entity.entity_id)
            _async_detach_device(device_reg, device, entry.entry_id)
            break


@callback
def _async_detach_device(device_reg: dr.DeviceRegistry, device, entry_id: str) -> None:
    """Remove this entry's claim on a device, leaving other entries' devices alone.

    Newer cores give every config entry its own devices (DeviceEntry.config_entry_id), so a
    device found through this entry is this entry's to remove. Older cores share a device
    between entries; there only this entry is detached, and the device goes once nothing
    else provides it.
    """
    if hasattr(device, "config_entry_id") or device.config_entries <= {entry_id}:
        device_reg.async_remove_device(device.id)
    else:
        device_reg.async_update_device(device.id, remove_config_entry_id=entry_id)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device) -> bool:
    """Allow deleting a device from the UI once the server stops reporting it.

    Without this, Home Assistant offers no Delete button at all and a job or repository that
    no longer exists can only be disabled. Deletion is refused while the object is still
    being reported, because the next poll would recreate it.
    """
    runtime = getattr(entry, "runtime_data", None) or {}
    coordinator = runtime.get("coordinator")
    data = getattr(coordinator, "data", None)

    if device_is_current(device.identifiers, data, entry.entry_id):
        _LOGGER.debug(
            "Refusing to remove %s: the server still reports it, so it would come back",
            device.name,
        )
        return False

    _LOGGER.info("Removing device %s at the user's request", device.name)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup for Microsoft 365 from a config entry."""
    # "auto" is stored as the user's intent, not a version, so it is resolved on every setup
    # — which means a restart picks up a server upgrade or a newer veeam-365 automatically.
    stored_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    if stored_version == AUTO_API_VERSION:
        api_version = await async_resolve_api_version(
            hass, {**entry.data, CONF_API_VERSION: AUTO_API_VERSION}
        )
        _LOGGER.info(
            "API version is set to auto; using %s for %s", api_version, entry.data[CONF_HOST]
        )
    else:
        api_version = stored_version

    # Convert display version (e.g., "8") to module version (e.g., "v8") for VeeamClient
    api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

    # Everything the SDK imports lazily is imported here, off the event loop, and the
    # models are patched to tolerate nulls before anything parses a response
    try:
        sdk = await hass.async_add_executor_job(load_sdk, api_module)
    except ImportError as err:
        raise ConfigEntryError(
            f"The installed veeam-365 cannot load API {api_module}: {describe_error(err)}"
        ) from err

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    veeam_client = create_client(sdk, entry.data)

    _LOGGER.debug("Connecting to Veeam server at %s:%s (api_version=%s)", host, port, api_module)
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            await veeam_client.connect()
    except VeeamAuthenticationError as err:
        await veeam_client.close()
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="authentication_failed",
            translation_placeholders={"username": str(entry.data.get(CONF_USERNAME, ""))},
        ) from err
    except (TimeoutError, VeeamError, httpx.HTTPError, OSError) as err:
        # Unreachable, timing out, or answering 5xx: all worth retrying later, none of them
        # a verdict on the credentials
        await veeam_client.close()
        _LOGGER.debug("Could not connect to %s:%s: %s", host, port, describe_error(err))
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="connection_error",
            translation_placeholders={
                "host": str(host),
                "port": str(port),
                "error": describe_error(err),
            },
        ) from err
    _LOGGER.info("Connected to Veeam server at %s:%s", host, port)

    coordinator = VeeamCoordinator(hass, entry, veeam_client, sdk)
    entry.runtime_data = {
        "coordinator": coordinator,
        "veeam_client": veeam_client,
        # Platforms and entities read the resolved version from here rather than re-reading
        # the entry, which may only hold "auto"
        "api_version": api_version,
    }

    try:
        # Also raises the unsupported-license repair when it applies (see coordinator)
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await veeam_client.close()
        raise

    @callback
    def _prune() -> None:
        async_prune_stale_devices(hass, entry, coordinator.data)

    _prune()
    entry.async_on_unload(coordinator.async_add_listener(_prune))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data["veeam_client"].close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up after a removed config entry.

    Deleted here rather than on unload, which also runs on every reload — the warning would
    otherwise disappear and come back on each restart.
    """
    ir.async_delete_issue(hass, DOMAIN, license_issue_id(entry))
