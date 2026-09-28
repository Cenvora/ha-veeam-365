"""Tests for the null- and unknown-value tolerance patches.

These run against the real installed veeam-365 models, so they verify the patch fixes the
failure rather than just that the code is present. Model modules are imported under
throwaway names so patches do not leak between tests.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest
from veeam_365.versions import VERSION_TO_PACKAGE

from custom_components.veeam_365 import sdk_patches

PACKAGES = sorted(set(VERSION_TO_PACKAGE.values()))


def fresh_model_module(package: str, name: str):
    spec = importlib.util.find_spec(f"{package}.models.{name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unset_of(package: str):
    return importlib.import_module(f"{package}.types").UNSET


def job(**overrides):
    payload = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Daily Mail",
        "lastRun": "2026-09-27T05:00:00+00:00",
        "nextRun": "2026-09-28T05:00:00+00:00",
        "isEnabled": True,
        "lastStatus": "Success",
        "backupType": "EntireOrganization",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("package", PACKAGES)
@pytest.mark.parametrize("null_field", ["nextRun", "lastRun"])
def test_null_timestamp_fails_before_patch_and_parses_after(package, null_field):
    module = fresh_model_module(package, "rest_job")
    payload = job(**{null_field: None})

    # Declared nullable here, so only check that the patched module still parses it
    sdk_patches.patch_null_values(module, unset_of(package))
    parsed = module.RESTJob.from_dict(payload)

    assert parsed.name == "Daily Mail"


@pytest.mark.parametrize("package", PACKAGES)
def test_null_enum_fails_before_patch_and_parses_after(package):
    """A null enum used to raise ValueError and wipe the whole endpoint."""
    payload = job(lastStatus=None)
    with pytest.raises((ValueError, TypeError)):
        fresh_model_module(package, "rest_job").RESTJob.from_dict(payload)

    module = fresh_model_module(package, "rest_job")
    unset = unset_of(package)
    sdk_patches.patch_null_values(module, unset)

    assert module.RESTJob.from_dict(payload).last_status is unset


@pytest.mark.parametrize("package", PACKAGES)
def test_unknown_enum_value_is_kept_as_the_raw_string(package):
    """A server newer than the SDK's schema sends values the enum does not list."""
    payload = job(lastStatus="SomethingNew")
    with pytest.raises(ValueError):
        fresh_model_module(package, "rest_job").RESTJob.from_dict(payload)

    module = fresh_model_module(package, "rest_job")
    sdk_patches.patch_null_values(module, unset_of(package))

    assert module.RESTJob.from_dict(payload).last_status == "SomethingNew"


@pytest.mark.parametrize("package", PACKAGES)
def test_null_nested_object_parses_as_unset(package):
    module = fresh_model_module(package, "rest_job")
    unset = unset_of(package)
    sdk_patches.patch_null_values(module, unset)

    assert module.RESTJob.from_dict(None) is unset


def test_known_values_are_untouched():
    module = fresh_model_module("veeam_365.v8", "rest_job")
    sdk_patches.patch_null_values(module, unset_of("veeam_365.v8"))
    parsed = module.RESTJob.from_dict(job())

    assert parsed.last_status.value == "Success"
    assert parsed.last_run.year == 2026


def test_patch_is_idempotent():
    module = fresh_model_module("veeam_365.v8", "rest_job")
    unset = unset_of("veeam_365.v8")

    assert sdk_patches.patch_null_values(module, unset) is True
    assert sdk_patches.patch_null_values(module, unset) is False


def test_enum_members_are_still_reachable_through_the_patched_name():
    module = fresh_model_module("veeam_365.v8", "rest_job")
    sdk_patches.patch_null_values(module, unset_of("veeam_365.v8"))

    assert module.RESTJobLastStatus.SUCCESS.value == "Success"


def test_a_non_object_payload_is_reported_with_the_model_and_the_value():
    module = fresh_model_module("veeam_365.v8", "rest_job")
    sdk_patches.patch_null_values(module, unset_of("veeam_365.v8"))

    with pytest.raises(TypeError, match="RESTJob expected a JSON object.*'oops'"):
        module.RESTJob.from_dict("oops")


def test_patch_models_ignores_other_packages_and_none_entries():
    module = fresh_model_module("veeam_365.v8", "rest_job")
    modules = {
        "veeam_365.v8.models.rest_job": module,
        "veeam_365.v8.models.missing": None,
        "veeam_365.v7.models.rest_job": fresh_model_module("veeam_365.v7", "rest_job"),
    }

    assert sdk_patches.patch_models("veeam_365.v8.models", unset_of("veeam_365.v8"), modules) == 1
