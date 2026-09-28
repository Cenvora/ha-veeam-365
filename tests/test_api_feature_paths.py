"""The operations the integration calls exist in the API versions it expects them in.

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


def _version_number(api_module: str) -> int:
    return int(api_module.removeprefix("v"))


@pytest.mark.parametrize("api_module", MODULES)
@pytest.mark.parametrize("operation", sdk.OPERATIONS)
def test_every_operation_exists(api_module, operation):
    """Versioned operations exist from their first version on, and not before.

    Absent before it too: an operation that turned out to exist everywhere belongs with the
    unconditional ones, and its has_operation gate is dead code.
    """
    try:
        spec = importlib.util.find_spec(f"veeam_365.{api_module}.api.{operation}")
    except ModuleNotFoundError:
        spec = None  # the whole API module (tag) is missing, not just the operation
    since = sdk.VERSIONED_OPERATIONS.get(operation)
    if since is None or _version_number(api_module) >= _version_number(since):
        assert spec is not None, f"{api_module} has no {operation}"
    else:
        assert spec is None, f"{operation} exists before {since}; it needs no version gate"


def test_versioned_operations_name_a_real_version():
    for operation, since in sdk.VERSIONED_OPERATIONS.items():
        assert since in MODULES, f"{operation} is gated on unknown version {since}"


@pytest.mark.parametrize(
    "description",
    [*button.JOB_BUTTONS, *button.COPY_JOB_BUTTONS, *button.REPOSITORY_BUTTONS],
    ids=lambda description: description.operation,
)
def test_buttons_only_call_loaded_operations(description):
    assert description.operation in sdk.ACTION_OPERATIONS


@pytest.mark.parametrize(
    "description", button.ORGANIZATION_BUTTONS, ids=lambda description: description.operation
)
def test_versioned_buttons_are_loaded_where_they_exist(description):
    assert description.operation in sdk.VERSIONED_OPERATIONS


@pytest.mark.parametrize("api_module", MODULES)
def test_button_id_arguments_match_the_operations(api_module):
    """job_* actions take job_id, copy jobs take id, repositories repository_id."""
    loaded = sdk.load_sdk(api_module)
    for description in (
        *button.JOB_BUTTONS,
        *button.COPY_JOB_BUTTONS,
        *button.REPOSITORY_BUTTONS,
        *button.ORGANIZATION_BUTTONS,
    ):
        if not loaded.has_operation(description.operation):
            # A version without it gets no button (see button.async_setup_entry)
            assert description.operation in sdk.VERSIONED_OPERATIONS
            continue
        assert loaded.accepts(description.operation, description.id_param), (
            api_module,
            description.operation,
        )
        if loaded.accepts(description.operation, "body"):
            assert (
                description.start_options or description.body_fn
            ), f"{description.operation} needs a body"


def test_only_v8_collections_are_paged():
    assert sdk.load_sdk("v8").accepts("job.job_get", "limit")
    assert not sdk.load_sdk("v7").accepts("job.job_get", "limit")


@pytest.mark.parametrize("api_module", ["v7", "v8"])
def test_the_organization_sync_body_builds_on_each_version(api_module):
    (description,) = button.ORGANIZATION_BUTTONS
    body = description.body_fn(sdk.load_sdk(api_module).models)
    assert body.to_dict() == {"type": "Incremental"}
