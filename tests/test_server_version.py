"""Reading the VB365 product version, which gates endpoints newer builds of one API add."""

from __future__ import annotations

import pytest

from custom_components.veeam_365.coordinator import (
    MAINTENANCE_MIN_SERVER_VERSION,
    parse_version,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8.1.0.305", (8, 1, 0, 305)),
        ("8.6", (8, 6)),
        (" 8.6.0.1004 ", (8, 6, 0, 1004)),
        ("8.6.0.1004-beta", (8, 6, 0, 1004)),
        ("", None),
        (None, None),
        ("unknown", None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    ("text", "supported"),
    [
        ("8.1.0.305", False),
        ("8.5.9.9999", False),
        ("8.6", True),
        ("8.6.0.1004", True),
        ("8.10.0.1", True),
        ("9.0.0.1", True),
    ],
)
def test_maintenance_needs_8_6(text, supported):
    assert (parse_version(text) >= MAINTENANCE_MIN_SERVER_VERSION) is supported
