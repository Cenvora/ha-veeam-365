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
        # coordinator.data may be a dict with a "jobs" key or a flat list of jobs
        "jobs_count": (
            len(
                coordinator.data.get("jobs")
                if isinstance(coordinator.data, dict)
                else coordinator.data
            )
            if coordinator.data
            else 0
        ),
    }

    # Add sanitized job information
    if coordinator.data:
        # Support both the new dict structure and the legacy flat list structure
        jobs = (
            coordinator.data.get("jobs") if isinstance(coordinator.data, dict) else coordinator.data
        )
        if jobs:
            jobs_info = []
            for job in jobs:
                # Ensure we only process dict-like job entries
                if not isinstance(job, dict):
                    continue
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

        # Add license information if available
        if isinstance(coordinator.data, dict):
            license_data = coordinator.data.get("license")
            if license_data:
                diagnostics_data["license"] = {
                    "license_id": license_data.get("license_id"),
                    "status": license_data.get("status"),
                    "type": license_data.get("type"),
                    "support_id": license_data.get("support_id"),
                    "licensed_to": license_data.get("licensed_to"),
                    "license_expires": (
                        str(license_data.get("license_expires"))
                        if license_data.get("license_expires")
                        else None
                    ),
                }

    return diagnostics_data
