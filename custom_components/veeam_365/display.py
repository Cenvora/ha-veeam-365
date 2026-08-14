"""Turn Veeam's API values into something readable.

The REST API reports enum values as identifiers — ``EntireOrganization``, ``NotConfigured``,
``AmazonS3Glacier``, ``ItemLevel`` — which is fine for code and poor on a dashboard.

Two rules, in order:

1. An explicit override, for values where splitting the identifier gives the wrong answer:
   ``AwsPrivateOffer`` is "AWS private offer", not "Aws Private Offer".
2. Otherwise split the identifier into words and title-case them, keeping runs of capitals
   intact so ``IBMCloudRepository`` becomes "IBM Cloud Repository".

Matching is case-insensitive on purpose. Veeam is not consistent between versions, and a
display layer that renders the same state two different ways depending on the server is worse
than useless.

The raw value is kept alongside the label wherever this is used, so automations that need to
match exactly have something stable to match on.

Kept free of Home Assistant imports so it can be tested directly.
"""

from __future__ import annotations

import re
from typing import Any

# Words that should never be title-cased into something silly
ACRONYMS = frozenset(
    {
        "AD",
        "API",
        "AWS",
        "GB",
        "IBM",
        "ID",
        "IP",
        "NFR",
        "S3",
        "SMB",
        "SQL",
        "TB",
        "URL",
    }
)

# Values whose split would read badly, or that have an established spelling
OVERRIDES = {
    # License types (RESTLicenseType)
    "awsprivateoffer": "AWS private offer",
    "nfr": "NFR",
    "community": "Community",
    # Job backup types (RESTJobBackupType)
    "entireorganization": "Entire organization",
    "selecteditems": "Selected items",
    # Job and copy job statuses (RESTJobLastStatus, RESTCopyJobLastStatus)
    "notconfigured": "Not configured",
    # Session results. "None" on its own reads as a missing value rather than "never ran"
    "none": "No result",
    # Object storage types (RESTObjectStorageType)
    "amazons3": "Amazon S3",
    "amazons3compatible": "Amazon S3 compatible",
    "amazons3glacier": "Amazon S3 Glacier",
    "azureblob": "Azure Blob",
    "azureblobarchive": "Azure Blob Archive",
    "ibmcloud": "IBM Cloud",
    "wasabicloud": "Wasabi Cloud",
    # Repository retention (RESTBackupRepositoryRetentionType)
    "itemlevel": "Item level",
    "snapshotbased": "Snapshot-based",
    # Licensed user states (RESTLicensedUserLicenseState)
    "temporarilyassigned": "Temporarily assigned",
    # Detected Microsoft 365 SKU types
    "unspecified": "Unspecified",
}

# Split on underscores, hyphens, spaces, lower→upper boundaries, and the end of a run of
# capitals that is followed by a normal word (so "IBMCloud" splits as "IBM" + "Cloud")
_SPLIT = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _word(word: str) -> str:
    if word.upper() in ACRONYMS:
        return word.upper()
    if word.isupper() and len(word) > 1:
        # An unlisted all-caps run is probably still an acronym
        return word
    return word[:1].upper() + word[1:]


def humanize(value: Any, default: str | None = None) -> str | None:
    """Return a readable label for an API value.

    Anything that is not a non-empty string — None, the library's UNSET sentinel, a number —
    returns ``default``, so callers can decide between "Unknown" and leaving a sensor empty.
    """
    if not isinstance(value, str):
        return default

    text = value.strip()
    if not text:
        return default

    # Looked up with separators removed too, so "item-level" and "ItemLevel" agree
    lowered = text.lower()
    override = OVERRIDES.get(lowered) or OVERRIDES.get(re.sub(r"[_\-\s]+", "", lowered))
    if override:
        return override

    words = [word for word in _SPLIT.split(text) if word]
    if not words:
        return default

    return " ".join(_word(word) for word in words)
