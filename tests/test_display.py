"""Tests for turning Veeam's API values into readable labels.

display.py imports no Home Assistant modules, so it runs directly. The inputs below are real
values from the generated enums (RESTJobBackupType, RESTJobLastStatus, RESTCopyJobLastStatus,
RESTLicenseType, RESTObjectStorageType, RESTBackupRepositoryRetentionType) rather than
invented ones.
"""

import importlib.util
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_365"


def _load_display():
    spec = importlib.util.spec_from_file_location("veeam_365_display", COMPONENT / "display.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="humanize", scope="module")
def humanize_fixture():
    return _load_display().humanize


@pytest.mark.parametrize(
    "value,expected",
    [
        # Job backup types (RESTJobBackupType)
        ("EntireOrganization", "Entire organization"),
        ("SelectedItems", "Selected items"),
        # Job and copy job statuses
        ("Success", "Success"),
        ("Running", "Running"),
        ("Failed", "Failed"),
        ("Warning", "Warning"),
        ("Stopped", "Stopped"),
        ("Queued", "Queued"),
        ("Disconnected", "Disconnected"),
        ("NotConfigured", "Not configured"),
        # License types (RESTLicenseType)
        ("Community", "Community"),
        ("Evaluation", "Evaluation"),
        ("Rental", "Rental"),
        ("Subscription", "Subscription"),
        ("NFR", "NFR"),
        ("AwsPrivateOffer", "AWS private offer"),
        # Licensed user states
        ("TemporarilyAssigned", "Temporarily assigned"),
        ("Exceeded", "Exceeded"),
    ],
)
def test_reads_like_english(humanize, value, expected):
    assert humanize(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("AmazonS3", "Amazon S3"),
        ("AmazonS3Compatible", "Amazon S3 compatible"),
        ("AmazonS3Glacier", "Amazon S3 Glacier"),
        ("AzureBlob", "Azure Blob"),
        ("AzureBlobArchive", "Azure Blob Archive"),
        ("IBMCloud", "IBM Cloud"),
        ("WasabiCloud", "Wasabi Cloud"),
        ("ItemLevel", "Item level"),
        ("SnapshotBased", "Snapshot-based"),
    ],
)
def test_overrides_beat_the_generic_split(humanize, value, expected):
    """Splitting these produces something wrong or ugly, so they are spelled out."""
    assert humanize(value) == expected


@pytest.mark.parametrize(
    "value",
    ["notconfigured", "NOTCONFIGURED", "not_configured", "not-configured", " NotConfigured "],
)
def test_matching_is_case_and_separator_insensitive(humanize, value):
    """Veeam is not consistent between versions, and one state must render one way."""
    assert humanize(value) == "Not configured"


def test_an_unknown_value_still_gets_a_label(humanize):
    """A type added in a future version should read correctly without a code change."""
    assert humanize("SomeFutureBackupType") == "Some Future Backup Type"


def test_runs_of_capitals_are_kept_intact(humanize):
    assert humanize("IBMCloudRepository") == "IBM Cloud Repository"


@pytest.mark.parametrize("value", [None, "", "   ", 42, object()])
def test_non_strings_fall_back_to_the_default(humanize, value):
    """UNSET sentinels, numbers and blanks are the caller's decision, not this module's."""
    assert humanize(value) is None
    assert humanize(value, "Unknown") == "Unknown"


def test_the_raw_value_is_never_mutated(humanize):
    """Callers keep the API value alongside the label; this must not alter it."""
    value = "EntireOrganization"

    humanize(value)

    assert value == "EntireOrganization"
