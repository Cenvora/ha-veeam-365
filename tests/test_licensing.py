"""Tests for unsupported-license detection.

licensing.py imports no Home Assistant modules, so it runs directly. The values here are the
real RESTLicenseType strings the API returns.
"""

import importlib.util
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_365"


def _load_licensing():
    """Load licensing.py standalone."""
    spec = importlib.util.spec_from_file_location("veeam_365_licensing", COMPONENT / "licensing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def license_info(type_="Subscription", status="Valid"):
    """Shaped like coordinator.data["license_info"], which keeps label and raw value."""
    return {"status": status, "type": type_, "type_raw": type_}


# RESTLicenseType: AwsPrivateOffer, Community, Evaluation, NFR, Rental, Subscription


@pytest.mark.parametrize(
    "type_",
    ["Subscription", "Rental", "Evaluation", "NFR", "AwsPrivateOffer"],
)
def test_supported_licenses_are_not_flagged(type_):
    """Paid, evaluation and NFR licenses all entitle the endpoints this integration uses."""
    licensing = _load_licensing()

    assert licensing.unsupported_license_reason(license_info(type_)) is None


def test_community_edition_is_flagged():
    licensing = _load_licensing()

    reason = licensing.unsupported_license_reason(license_info("Community"))

    assert reason == licensing.REASON_COMMUNITY_EDITION


def test_no_license_is_reported_separately():
    """A server reporting nothing installed is worth naming distinctly."""
    licensing = _load_licensing()

    assert (
        licensing.unsupported_license_reason(license_info("Empty")) == licensing.REASON_NO_LICENSE
    )
    assert licensing.unsupported_license_reason(license_info("None")) == licensing.REASON_NO_LICENSE


def test_the_type_is_matched_case_insensitively():
    """Casing is Veeam's to change; detection should not hinge on it."""
    licensing = _load_licensing()

    assert licensing.unsupported_license_reason(license_info("COMMUNITY")) is not None
    assert licensing.unsupported_license_reason(license_info(" community ")) is not None


def test_the_raw_value_is_preferred_over_the_label():
    """The label is a display concern and could be reworded without anyone updating this."""
    licensing = _load_licensing()

    info = {"status": "Valid", "type": "Some Friendly Wording", "type_raw": "Community"}

    assert licensing.unsupported_license_reason(info) == licensing.REASON_COMMUNITY_EDITION


def test_the_label_is_used_when_no_raw_value_was_kept():
    licensing = _load_licensing()

    assert (
        licensing.unsupported_license_reason({"status": "Valid", "type": "Community"})
        == licensing.REASON_COMMUNITY_EDITION
    )


@pytest.mark.parametrize(
    "info",
    [None, {}, {"status": "Valid"}, {"type": None}, {"type": 12}],
    ids=["none", "empty", "status-only", "null-field", "wrong-type"],
)
def test_unreadable_license_is_not_flagged(info):
    """Missing license data is its own failure, logged where it happens.

    Guessing "unlicensed" from an absent license would warn people who simply hit an API
    error, which is worse than staying quiet.
    """
    licensing = _load_licensing()

    assert licensing.unsupported_license_reason(info) is None


def test_describe_license_summarizes_for_a_log_line():
    licensing = _load_licensing()

    assert licensing.describe_license(license_info("Community")) == "Community/Valid"
    assert licensing.describe_license(None) == "unknown"
    assert licensing.describe_license({}) == "unknown"
    assert "Unknown" in licensing.describe_license({"type": None, "status": None})


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_warning_is_raised_as_a_repair_issue_and_logged():
    """A log line alone is invisible; repairs surface in the UI."""
    content = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    check = content[content.index("def _check_license_support") :]
    check = check[: check.index("async def async_setup_entry")]

    assert "ir.async_create_issue" in check, "should raise a repair issue"
    assert "_LOGGER.warning" in check, "should also log, for reload visibility"
    assert "IssueSeverity.WARNING" in check, "a warning, not an error: setup still works"
    assert "ir.async_delete_issue" in check, "should clear once the license is supported"
    assert "return False" not in check, "an unsupported license must not block setup"


def test_license_is_checked_on_every_setup():
    """Setup runs again on reload, which is how the warning reappears."""
    content = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    first_refresh = content.index("await coordinator.async_config_entry_first_refresh()")
    check = content.index("_check_license_support(hass, entry, coordinator.data)")
    forward = content.index("async_forward_entry_setups")

    assert first_refresh < check < forward, (
        "the check needs coordinator data, so it belongs after the first refresh and before "
        "platforms are set up"
    )


def test_the_issue_is_cleared_when_the_entry_is_removed():
    """Deleted on removal rather than unload, which also runs on every reload."""
    content = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    remove = content[content.index("async def async_remove_entry") :]

    assert "ir.async_delete_issue" in remove


def test_the_repair_issue_has_translations():
    """An untranslated repair issue renders as a raw key."""
    import json

    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))

    for source in (strings, english):
        issue = source["issues"]["unsupported_license"]
        assert "{host}" in issue["title"]
        assert "{license}" in issue["description"]
