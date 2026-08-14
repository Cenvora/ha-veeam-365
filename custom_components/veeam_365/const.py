"""Constants for the Veeam Backup for Microsoft 365 integration."""

import importlib.util
import logging
import os
import re

DOMAIN = "veeam_365"
DEFAULT_NAME = "Veeam Backup for Microsoft 365"

# Configuration keys
CONF_VERIFY_SSL = "verify_ssl"
CONF_API_VERSION = "api_version"

# Defaults
# The REST API service listens on 4443 out of the box. It is configurable in the console, so
# this only pre-fills the form; existing entries keep whatever port they were created with.
DEFAULT_PORT = 4443
DEFAULT_VERIFY_SSL = True
# Newest API version shipped by veeam-365, served by VB365 v8. Bump this together with
# FALLBACK_API_VERSIONS when veeam-365 adds a version. Users on older servers can select an
# older version in the config flow; the flow validates the connection, so a version the
# server does not serve fails at setup rather than silently.
DEFAULT_API_VERSION = "8"

# Selector sentinel: probe the server for the newest API version it serves (see
# api_version.py). Stored as-is rather than resolved once, so a server upgrade — or a
# veeam-365 release that adds a newer version — is picked up on the next restart.
AUTO_API_VERSION = "auto"

_LOGGER = logging.getLogger(__name__)

# Fallback used when the veeam-365 package cannot be inspected. Mirrors the versions shipped
# by veeam-365 (see veeam_365.versions.VERSION_TO_PACKAGE).
FALLBACK_API_VERSIONS = {
    "8": "v8",
    "7": "v7",
    "6": "v6",
}

# Package directory backing DEFAULT_API_VERSION, used when a stored API version is unknown
DEFAULT_API_MODULE = FALLBACK_API_VERSIONS[DEFAULT_API_VERSION]


# Pattern to match version directories: v{major}
_API_VERSION_PATTERN = re.compile(r"^v(\d+)$")


def _discover_api_versions() -> dict[str, str]:
    """Dynamically discover available API versions from the veeam_365 package.

    Returns:
        dict: Mapping of display version (e.g., "8") to module name (e.g., "v8"),
            ordered newest to oldest.
    """
    discovered: list[tuple[int, str, str]] = []

    try:
        # Find the veeam_365 package
        spec = importlib.util.find_spec("veeam_365")
        if spec is None:
            _LOGGER.warning("veeam_365 package not found, using default API versions")
            return dict(FALLBACK_API_VERSIONS)

        # Get the package directory (handle namespace packages)
        if spec.submodule_search_locations:
            veeam_365_path = spec.submodule_search_locations[0]
        elif spec.origin:
            veeam_365_path = os.path.dirname(spec.origin)
        else:
            _LOGGER.warning("Could not determine veeam_365 package path, using defaults")
            return dict(FALLBACK_API_VERSIONS)

        # Scan for version directories
        for item in os.listdir(veeam_365_path):
            match = _API_VERSION_PATTERN.match(item)
            if match and os.path.isdir(os.path.join(veeam_365_path, item)):
                major = match.group(1)
                # Convert to display format: "8"
                discovered.append((int(major), major, item))

        if not discovered:
            _LOGGER.warning("No API versions found in veeam_365 package, using defaults")
            return dict(FALLBACK_API_VERSIONS)

        # Sort numerically so the selector order does not depend on filesystem ordering,
        # newest first — the version most people want is then the one at the top
        versions = {display: module for _, display, module in sorted(discovered, reverse=True)}

        _LOGGER.debug("Discovered API versions: %s", list(versions.keys()))

    except Exception as err:
        _LOGGER.warning("Failed to discover API versions: %s, using defaults", err)
        return dict(FALLBACK_API_VERSIONS)

    return versions


def display_version_for_module(api_module: str) -> str | None:
    """Map a veeam-365 package directory ("v8") back to a display version ("8").

    Detection reports the module name, because that is what the library's version table is
    keyed by, while config entries store the display version.
    """
    for display, module in API_VERSIONS.items():
        if module == api_module:
            return display
    return None


# API Version options - dynamically discovered from veeam_365 package
API_VERSIONS = _discover_api_versions()

# Update interval
UPDATE_INTERVAL = 60  # seconds


def check_api_feature_availability(api_version: str, feature_path: str) -> bool:
    """Check if a specific API feature (endpoint/spec model) is available in the given API version.

    Args:
        api_version: The API version to check (e.g., "8")
        feature_path: The import path to check (e.g., "models.job_start_spec" or "api.job")

    Returns:
        bool: True if the feature is available in the API version, False otherwise
    """
    api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

    try:
        # Try to import the module/feature
        import_path = f"veeam_365.{api_module}.{feature_path}"
        spec = importlib.util.find_spec(import_path)
        return spec is not None
    except (ImportError, ModuleNotFoundError, ValueError, AttributeError):
        return False


# API feature requirements mapping
# This mapping documents which API features (models/endpoints) are required for each entity type.
# It serves as reference documentation for developers - feature paths are used directly
# in button.py and sensor.py via check_api_feature_availability() calls.
API_FEATURE_REQUIREMENTS = {
    # Button features - buttons check for API endpoint availability, not individual models
    # Individual button methods handle model import errors gracefully at runtime
    "job_buttons": "api.job",  # Enables all job buttons (start, stop, retry, enable, disable)
    "copy_job_buttons": "api.copy_job",  # Enables all copy job buttons
    "repository_buttons": "api.backup_repository",  # Enables repository synchronize button
    # Data sources (for sensors)
    "jobs_data": "api.job",
    "copy_jobs_data": "api.copy_job",
    "repositories_data": "api.backup_repository",
    "license_data": "api.license_",
}


def configured_api_version(entry) -> str:
    """The API version to talk to this server with.

    CONF_API_VERSION may hold AUTO_API_VERSION, which is a user intent rather than a version:
    it means "use the newest version this server serves", resolved during setup so a server
    upgrade or a newer veeam-365 is picked up on the next restart. The resolved value is kept
    in entry.runtime_data, so platforms and entities read it from there rather than
    re-detecting.

    Falls back to DEFAULT_API_VERSION when asked before setup has resolved anything, which is
    the same answer detection would give if probing found nothing.
    """
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, dict):
        resolved = runtime.get("api_version")
        if resolved and resolved != AUTO_API_VERSION:
            return resolved

    stored = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    return DEFAULT_API_VERSION if stored == AUTO_API_VERSION else stored
