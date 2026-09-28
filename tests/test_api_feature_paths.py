"""The operations the integration calls exist in every API version it offers.

The buttons used to import model modules (job_start_action and friends) that exist in no
version of veeam-365; the ImportError was swallowed and every button did nothing. These
check the real operation modules instead, and that nothing else is looked up by name.
"""

from __future__ import annotations

import importlib.util

import pytest

from custom_components.veeam_365 import button, sdk
from custom_components.veeam_365.const import API_VERSIONS

MODULES = sorted(API_VERSIONS.values())


@pytest.mark.parametrize("api_module", MODULES)
@pytest.mark.parametrize("operation", sdk.OPERATIONS)
def test_every_operation_exists(api_module, operation):
    spec = importlib.util.find_spec(f"veeam_365.{api_module}.api.{operation}")
    assert spec is not None, f"{api_module} has no {operation}"


@pytest.mark.parametrize(
    "description",
    [*button.JOB_BUTTONS, *button.COPY_JOB_BUTTONS, *button.REPOSITORY_BUTTONS],
    ids=lambda description: description.operation,
)
def test_buttons_only_call_loaded_operations(description):
    assert description.operation in sdk.ACTION_OPERATIONS


@pytest.mark.parametrize("api_module", MODULES)
def test_button_id_arguments_match_the_operations(api_module):
    """job_* actions take job_id, copy jobs take id, repositories repository_id."""
    loaded = sdk.load_sdk(api_module)
    for description in (*button.JOB_BUTTONS, *button.COPY_JOB_BUTTONS, *button.REPOSITORY_BUTTONS):
        assert loaded.accepts(description.operation, description.id_param), (
            api_module,
            description.operation,
        )
        if loaded.accepts(description.operation, "body"):
            assert description.start_options, f"{description.operation} needs a body"


def test_only_v8_collections_are_paged():
    assert sdk.load_sdk("v8").accepts("job.job_get", "limit")
    assert not sdk.load_sdk("v7").accepts("job.job_get", "limit")
