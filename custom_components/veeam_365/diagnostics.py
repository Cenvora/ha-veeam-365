"""Diagnostics support for Veeam Backup for Microsoft 365."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_VERIFY_SSL


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator

    # Gather diagnostic information
    diagnostics_data = {
        "entry": {
            "title": entry.title,
            "domain": entry.domain,
            "version": entry.version,
        },
        "config": {
            "host": entry.data.get("host"),
            "port": entry.data.get("port"),
            "username": entry.data.get("username"),
            "verify_ssl": entry.data.get(CONF_VERIFY_SSL),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                str(coordinator.last_exception) if coordinator.last_exception else None
            ),
            "update_interval": str(coordinator.update_interval),
        },
        "jobs_count": len(coordinator.data) if coordinator.data else 0,
    }

    # Add sanitized job information
    if coordinator.data:
        jobs_info = []
        for job in coordinator.data:
            jobs_info.append(
                {
                    "id": job.get("id"),
                    "name": job.get("name"),
                    "status": job.get("status"),
                    "backup_type": job.get("backup_type"),
                    "is_enabled": job.get("is_enabled"),
                    "total_objects": job.get("total_objects"),
                    "processed_objects": job.get("processed_objects"),
                }
            )
        diagnostics_data["jobs"] = jobs_info

    return diagnostics_data
