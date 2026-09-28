"""Device names: "VB365 <kind> <name>", with the kind left out when the name already says it.

The B&R integration follows the same rule with a "VBR" prefix, so a repository both products
call "Default Backup Repository" gets two distinct entity IDs.
"""

import pytest

from custom_components.veeam_365.const import device_name


@pytest.mark.parametrize(
    ("kind", "name", "expected"),
    [
        ("Job", "Daily Mail", "VB365 Job Daily Mail"),
        ("Job", "Daily Mail Job", "VB365 Daily Mail Job"),
        ("Copy Job", "Mail Copy", "VB365 Copy Job Mail Copy"),
        ("Repository", "Default Backup Repository", "VB365 Default Backup Repository"),
        # A word that merely contains the kind is not the kind
        ("Job", "Jobsite Mailboxes", "VB365 Job Jobsite Mailboxes"),
        ("License", "veeam.example.com", "VB365 License veeam.example.com"),
        ("License", None, "VB365 License"),
    ],
)
def test_device_name(kind, name, expected):
    assert device_name(kind, name) == expected
