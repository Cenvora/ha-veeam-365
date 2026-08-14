"""Recognize Veeam licenses this integration does not support.

Veeam Backup for Microsoft 365 Community Edition, and a server whose license cannot be read
at all, are outside what this integration is tested against: entitlements differ, some
endpoints answer differently or not at all, and the resulting failures look like integration
bugs rather than licensing limits.

Detection is a warning, never a block. A Community Edition server that works is not worth
refusing, and the check is only as good as what the license endpoint reports — so it errs
toward saying nothing when the answer is unclear.

Kept free of Home Assistant imports so it can be tested directly.
"""

from __future__ import annotations

from typing import Any

# RESTLicenseType value for Community Edition, the free tier
COMMUNITY_EDITION = "community"

# License types that mean "not a paid license". Community Edition reports "Community"; some
# builds report an empty or free-shaped value instead of omitting the field.
UNLICENSED_TYPES = frozenset({"community", "free", "empty", "none"})

# Reason codes, used as translation placeholders and log detail
REASON_COMMUNITY_EDITION = "community_edition"
REASON_NO_LICENSE = "no_license"


def _normalize(value: Any) -> str:
    """Lower-case a license field, tolerating None and non-strings."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def unsupported_license_reason(license_info: dict[str, Any] | None) -> str | None:
    """Return why this license is unsupported, or None if it looks supported.

    Returns REASON_COMMUNITY_EDITION or REASON_NO_LICENSE. An absent or unreadable license
    returns None: the license endpoint failing is its own problem, already logged where it
    happens, and guessing "unlicensed" from missing data would warn people wrongly.
    """
    if not license_info:
        return None

    # The raw API value where it is kept, since the readable label is a display concern and
    # could be reworded without anyone thinking about this check
    license_type = _normalize(license_info.get("type_raw") or license_info.get("type"))

    if license_type == COMMUNITY_EDITION:
        return REASON_COMMUNITY_EDITION

    if license_type in UNLICENSED_TYPES:
        # "Empty" and "None" mean no license at all, which is worth naming separately
        return REASON_NO_LICENSE if license_type in {"empty", "none"} else REASON_COMMUNITY_EDITION

    return None


def describe_license(license_info: dict[str, Any] | None) -> str:
    """Summarize a license for a log line, e.g. "Community/Valid"."""
    if not license_info:
        return "unknown"

    license_type = license_info.get("type") or "Unknown"
    status = license_info.get("status") or "Unknown"
    return f"{license_type}/{status}"
