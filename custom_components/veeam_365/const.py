"""Constants for the Veeam Backup for Microsoft 365 integration."""

import logging
import re

DOMAIN = "veeam_365"
DEFAULT_NAME = "Veeam Backup for Microsoft 365"

# Every device this integration creates is named with this prefix. Entities use
# has_entity_name, so their entity IDs start with the device name — without a prefix of its
# own, "License" or "Server" collides with the Backup & Replication integration's devices
# and one of them gets a "_2" suffix.
DEVICE_NAME_PREFIX = "VB365"

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

# Timeouts, in seconds. REQUEST_TIMEOUT bounds a single HTTP request inside the SDK;
# UPDATE_TIMEOUT bounds a whole poll, which is several requests plus pagination, so a server
# that accepts connections but never answers cannot wedge the coordinator.
REQUEST_TIMEOUT = 30.0
CONNECT_TIMEOUT = 60.0
UPDATE_TIMEOUT = 180.0
ACTION_TIMEOUT = 60.0

# Page size for the paged v8 collection endpoints. The server default is 30, which silently
# truncated larger installations to their first 30 jobs.
PAGE_LIMIT = 100
# Hard stop for pagination, in case a server keeps answering with full pages forever
MAX_PAGES = 100

# Protected users, groups, sites and teams are counted by paging through every one of them:
# v8 pages carry no total. So they are counted hourly, on a coordinator of their own with its
# own time limit, in pages far larger than the minute-by-minute poll uses (the server allows
# up to 10,000). A large tenant can then neither slow the regular poll nor fail it.
# Job sessions: how far back the first poll after startup looks for each job's latest
# session (later polls only ask for what is new), and the overlap between polls that keeps a
# session created just as the previous poll ran from slipping through
JOB_SESSIONS_LOOKBACK_HOURS = 26
JOB_SESSIONS_OVERLAP_MINUTES = 10
JOB_SESSIONS_PAGE_LIMIT = 1000

PROTECTED_COUNT_INTERVAL = 3600  # seconds
PROTECTED_COUNT_TIMEOUT = 900.0
PROTECTED_PAGE_LIMIT = 1000

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

# veeam-365 names its API packages v{major}
_API_VERSION_PATTERN = re.compile(r"^v(\d+)$")


def _discover_api_versions() -> dict[str, str]:
    """Read the API versions veeam-365 ships from its own version table.

    Returns:
        dict: Mapping of display version (e.g., "8") to module name (e.g., "v8"),
            ordered newest to oldest.
    """
    try:
        from veeam_365.versions import VERSION_TO_PACKAGE
    except ImportError as err:
        _LOGGER.warning("Could not read veeam-365's version table (%r), using defaults", err)
        return dict(FALLBACK_API_VERSIONS)

    discovered: list[tuple[int, str, str]] = []
    for module in VERSION_TO_PACKAGE:
        match = _API_VERSION_PATTERN.match(module)
        if match:
            discovered.append((int(match.group(1)), match.group(1), module))

    if not discovered:
        _LOGGER.warning("veeam-365 reports no API versions, using defaults")
        return dict(FALLBACK_API_VERSIONS)

    # Sorted numerically, newest first — the version most people want is then at the top
    return {display: module for _, display, module in sorted(discovered, reverse=True)}


def display_version_for_module(api_module: str) -> str | None:
    """Map a veeam-365 package directory ("v8") back to a display version ("8").

    Detection reports the module name, because that is what the library's version table is
    keyed by, while config entries store the display version.
    """
    for display, module in API_VERSIONS.items():
        if module == api_module:
            return display
    return None


# API Version options, from veeam-365's own version table
API_VERSIONS = _discover_api_versions()

# Update interval
UPDATE_INTERVAL = 60  # seconds


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


def device_name(kind: str, name: str | None = None) -> str:
    """Name a device "VB365 <Kind> <name>", leaving out the kind when the name says it.

    "Daily Mail" becomes "VB365 Job Daily Mail", but "Daily Mail Job" becomes
    "VB365 Daily Mail Job" rather than "VB365 Job Daily Mail Job".
    """
    if not name:
        return f"{DEVICE_NAME_PREFIX} {kind}"
    if re.search(rf"\b{re.escape(kind)}\b", name, re.IGNORECASE):
        return f"{DEVICE_NAME_PREFIX} {name}"
    return f"{DEVICE_NAME_PREFIX} {kind} {name}"
