"""Diagnostics support for Veeam Backup for Microsoft 365."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_VERSION, DEFAULT_API_VERSION, configured_api_version


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data["coordinator"]

    # Get the coordinator data
    data = coordinator.data if coordinator.data else {}

    # Build diagnostics data
    diagnostics_data = {
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "unique_id": entry.unique_id,
            # Both, because "auto" on its own answers nothing in a bug report
            "configured_api_version": entry.options.get(
                CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
            ),
            "resolved_api_version": configured_api_version(entry),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                repr(coordinator.last_exception) if coordinator.last_exception else None
            ),
            "last_update_success_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
        },
        "data": {
            "jobs_count": len(data.get("jobs", [])),
            "copy_jobs_count": len(data.get("copy_jobs", [])),
            "repositories_count": len(data.get("repositories", [])),
            "proxies_count": len(data.get("proxies", [])),
            "proxy_pools_count": len(data.get("proxy_pools", [])),
            "organizations_count": len(data.get("organizations", [])),
            "has_server_info": data.get("server_info") is not None,
            "has_license_info": data.get("license_info") is not None,
            # Which endpoints answered on the last successful poll
            "fetch_ok": data.get("fetch_ok", {}),
        },
    }

    # Add server info (without sensitive data)
    if data.get("server_info"):
        server_info = data["server_info"]
        diagnostics_data["server"] = {
            "version": server_info.get("version"),
            "installation_id": server_info.get("installation_id"),
        }

    # Add license info (without sensitive data)
    if data.get("license_info"):
        license_info = data["license_info"]
        diagnostics_data["license"] = {
            "status": license_info.get("status"),
            "type": license_info.get("type"),
        }

    # The server's own health report (API v8)
    if data.get("health"):
        health = data["health"]
        diagnostics_data["health"] = {
            "status": health.get("status_raw"),
            "checks": health.get("checks", {}),
        }

    # Add job summaries (without sensitive details)
    if data.get("jobs"):
        jobs_summary = {}
        for job in data["jobs"]:
            status = job.get("last_status", "unknown")
            jobs_summary[status] = jobs_summary.get(status, 0) + 1
        diagnostics_data["jobs_summary"] = jobs_summary

    # Add copy job summaries (without sensitive details)
    if data.get("copy_jobs"):
        copy_jobs_summary = {}
        for copy_job in data["copy_jobs"]:
            status = copy_job.get("last_status", "unknown")
            copy_jobs_summary[status] = copy_jobs_summary.get(status, 0) + 1
        diagnostics_data["copy_jobs_summary"] = copy_jobs_summary

    # Add repository summaries (without sensitive details)
    if data.get("repositories"):
        repos_summary = {}
        for repo in data["repositories"]:
            repo_type = repo.get("type", "unknown")
            repos_summary[repo_type] = repos_summary.get(repo_type, 0) + 1
        diagnostics_data["repositories_summary"] = repos_summary

    # Proxy states, without host names
    if data.get("proxies"):
        proxies_summary: dict[str, int] = {}
        for proxy in data["proxies"]:
            status = proxy.get("status_raw") or "unknown"
            maintenance = proxy.get("maintenance_mode_raw")
            state = f"{status} (maintenance {maintenance})" if maintenance else status
            proxies_summary[state] = proxies_summary.get(state, 0) + 1
        diagnostics_data["proxies_summary"] = proxies_summary

    # Organization sync results, without organization names or errors (which can name users)
    if data.get("organization_sync"):
        sync_summary: dict[str, int] = {}
        for sync in data["organization_sync"].values():
            result = sync.get("last_result") or "never"
            sync_summary[result] = sync_summary.get(result, 0) + 1
        diagnostics_data["organization_sync_summary"] = sync_summary

    # Job sessions (API v8), by the status of each job's latest session
    if data.get("job_sessions"):
        sessions_summary: dict[str, int] = {}
        for session in data["job_sessions"].values():
            status = session.get("status_raw") or "unknown"
            sessions_summary[status] = sessions_summary.get(status, 0) + 1
        diagnostics_data["job_sessions_summary"] = sessions_summary

    # Repository maintenance (API v8), by the status of each repository's latest session
    if data.get("repository_maintenance"):
        maintenance_summary: dict[str, int] = {}
        for session in data["repository_maintenance"].values():
            status = session.get("status_raw") or "unknown"
            maintenance_summary[status] = maintenance_summary.get(status, 0) + 1
        diagnostics_data["repository_maintenance_summary"] = maintenance_summary

    # The event feed (API v8)
    event_feed = entry.runtime_data.get("event_feed")
    if event_feed is not None:
        diagnostics_data["event_feed"] = {
            **event_feed.diagnostics(),
            "covers_job_sessions": coordinator.event_feed_covers_job_sessions,
        }

    # Protected object totals across organizations (API v8)
    protected_counts = entry.runtime_data.get("protected_counts")
    if protected_counts is not None:
        counts_data = protected_counts.data or {}
        totals: dict[str, int] = {}
        for org_counts in (counts_data.get("counts") or {}).values():
            for kind, number in org_counts.items():
                totals[kind] = totals.get(kind, 0) + number
        diagnostics_data["protected_counts"] = {
            "last_update_success": protected_counts.last_update_success,
            "fetch_ok": counts_data.get("fetch_ok", {}),
            "counted_at": (
                counts_data["counted_at"].isoformat() if counts_data.get("counted_at") else None
            ),
            "totals": totals,
        }

    # Add diagnostics info
    if data.get("diagnostics"):
        diagnostics_data["integration_diagnostics"] = {
            "connected": data["diagnostics"].get("connected"),
            "health_ok": data["diagnostics"].get("health_ok"),
            "failed_endpoints": data["diagnostics"].get("failed_endpoints", []),
            "last_successful_poll": (
                data["diagnostics"]["last_successful_poll"].isoformat()
                if data["diagnostics"].get("last_successful_poll")
                else None
            ),
        }

    return diagnostics_data
