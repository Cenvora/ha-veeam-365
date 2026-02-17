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
DEFAULT_PORT = 4443
DEFAULT_VERIFY_SSL = True
DEFAULT_API_VERSION = "8"

_LOGGER = logging.getLogger(__name__)


def _discover_api_versions() -> dict[str, str]:
    """Dynamically discover available API versions from the veeam_365 package.

    Returns:
        dict: Mapping of display version (e.g., "8") to module name (e.g., "v8")
    """
    versions = {}

    try:
        # Find the veeam_365 package
        spec = importlib.util.find_spec("veeam_365")
        if spec is None:
            _LOGGER.warning("veeam_365 package not found, using default API versions")
            return {
                "8": "v8",
                "7": "v7",
                "6": "v6",
            }

        # Get the package directory (handle namespace packages)
        if spec.submodule_search_locations:
            veeam_365_path = spec.submodule_search_locations[0]
        elif spec.origin:
            veeam_365_path = os.path.dirname(spec.origin)
        else:
            _LOGGER.warning("Could not determine veeam_365 package path, using defaults")
            return {
                "8": "v8",
                "7": "v7",
                "6": "v6",
            }

        # Pattern to match version directories: v{major}
        api_version_pattern = re.compile(r"^v(\d+)$")

        # Scan for version directories
        for item in os.listdir(veeam_365_path):
            item_path = os.path.join(veeam_365_path, item)
            if os.path.isdir(item_path) and api_version_pattern.match(item):
                match = api_version_pattern.match(item)
                if match:
                    major = match.group(1)
                    # Convert to display format: "8"
                    display_version = f"{major}"
                    versions[display_version] = item

        if not versions:
            _LOGGER.warning("No API versions found in veeam_365 package, using defaults")
            return {
                "8": "v8",
                "7": "v7",
                "6": "v6",
            }

        _LOGGER.debug("Discovered API versions: %s", list(versions.keys()))

    except Exception as err:
        _LOGGER.warning("Failed to discover API versions: %s, using defaults", err)
        return {
            "8": "v8",
            "7": "v7",
            "6": "v6",
        }

    return versions


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
    api_module = API_VERSIONS.get(api_version, "v8")

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
