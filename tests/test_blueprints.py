"""Validation for the shipped automation blueprints.

These read the files without Home Assistant (test_blueprint_behaviour.py runs them) and check
the things that actually break a blueprint in the wild and that no YAML linter would catch: an `!input` that
was never declared, a declared input nothing uses (a UI field that does nothing), a
`source_url` that does not match where the file really lives (which breaks the import link),
and selectors pointing at a different integration.
"""

import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parent.parent
BLUEPRINT_DIR = REPO / "blueprints" / "automation" / "veeam_365"
REPO_SLUG = "Cenvora/ha-veeam-365"


class BlueprintLoader(yaml.SafeLoader):
    """SafeLoader that understands Home Assistant's blueprint tags."""


class Input:
    """Stands in for an !input tag so the document can be walked."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"!input {self.name}"


BlueprintLoader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))


def blueprint_files():
    return sorted(BLUEPRINT_DIR.glob("*.yaml"))


def load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=BlueprintLoader)


def walk(node):
    """Yield every value in a nested structure."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def used_inputs(document):
    return {node.name for node in walk(document) if isinstance(node, Input)}


def test_blueprints_exist():
    assert blueprint_files(), "no blueprints found — has the directory moved?"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_has_required_metadata(path):
    """Missing metadata makes a blueprint unimportable or anonymous in the UI."""
    document = load(path)
    meta = document.get("blueprint")

    assert meta, "no blueprint: block"
    assert meta.get("domain") == "automation"
    assert meta.get("name"), "needs a name for the blueprint list"
    assert meta.get("description"), "needs a description explaining which entities to pick"
    assert meta.get("input"), "an automation blueprint with no inputs is just an automation"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_source_url_matches_the_file_location(path):
    """The import link is built from source_url; a stale one imports the wrong file."""
    document = load(path)
    source_url = document["blueprint"].get("source_url")

    assert source_url, "needs source_url so Home Assistant can offer re-import"
    expected = f"https://github.com/{REPO_SLUG}/blob/main/{path.relative_to(REPO).as_posix()}"
    assert source_url == expected, f"expected {expected}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_every_input_is_declared(path):
    """An !input with no declaration fails at import time with a schema error."""
    document = load(path)
    declared = set(document["blueprint"]["input"])

    undeclared = used_inputs(document) - declared
    assert not undeclared, f"used but not declared: {sorted(undeclared)}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_every_declared_input_is_used(path):
    """A declared input nothing references is a UI field that silently does nothing."""
    document = load(path)
    declared = set(document["blueprint"]["input"])

    unused = declared - used_inputs(document)
    assert not unused, f"declared but never used: {sorted(unused)}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_is_a_runnable_automation(path):
    """Uses the current plural keys."""
    document = load(path)

    assert "triggers" in document, "no triggers"
    assert "actions" in document, "no actions"
    assert "trigger" not in document, "singular trigger: is the pre-2024.10 spelling"
    assert "action" not in document, "singular action: is the pre-2024.10 spelling"
    assert "condition" not in document, "singular condition: is the pre-2024.10 spelling"

    for entry in document["triggers"]:
        assert "trigger" in entry, f"trigger entry missing its platform: {entry}"
        assert "platform" not in entry, "platform: is the pre-2024.10 spelling"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_entity_selectors_target_this_integration(path):
    """A selector without the filter lists every entity in the user's system."""
    document = load(path)

    for name, spec in document["blueprint"]["input"].items():
        selector = spec.get("selector", {})
        if "entity" not in selector:
            continue
        filters = (selector["entity"] or {}).get("filter")
        assert filters, f"{name}: entity selector should filter to this integration"
        integrations = {f.get("integration") for f in filters}
        assert integrations == {"veeam_365"}, f"{name}: filters {integrations}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_notification_action_is_an_action_selector(path):
    """Hard-coding a notify service would tie the blueprint to one setup."""
    document = load(path)
    inputs = document["blueprint"]["input"]

    assert "notification_action" in inputs, "every blueprint should let the user choose"
    assert "action" in inputs["notification_action"]["selector"]


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_optional_inputs_have_defaults(path):
    """An input with no default is mandatory; that has to be deliberate."""
    document = load(path)

    mandatory = [
        name for name, spec in document["blueprint"]["input"].items() if "default" not in spec
    ]
    # The entities to watch and the action to run are the only things a user must supply
    allowed = {
        "job_status_sensors",
        "repository_sensors",
        "expiry_sensors",
        "used_sensor",
        "total_sensor",
        "proxy_online_sensors",
        "health_ok_sensors",
        "last_backup_sensors",
        "sync_sensors",
        "notification_action",
    }
    assert set(mandatory) <= allowed, f"unexpectedly mandatory: {sorted(set(mandatory) - allowed)}"


def test_job_blueprints_watch_last_status():
    """VB365 reports the pass/fail outcome of a run on the Last Status sensor.

    Unlike Backup & Replication, there is no separate Last Result sensor here, so the
    description has to point at the right one or the blueprint watches nothing useful.
    """
    for name in ("job_failed.yaml", "daily_backup_summary.yaml"):
        text = (BLUEPRINT_DIR / name).read_text(encoding="utf-8")
        assert "Last Status" in text, f"{name} should direct users to the Last Status sensor"


def test_status_matching_is_case_insensitive():
    """Veeam has shipped different casing for enums between API versions."""
    for name in ("job_failed.yaml", "daily_backup_summary.yaml"):
        text = (BLUEPRINT_DIR / name).read_text(encoding="utf-8")
        assert (
            "| lower" in text or "map('lower')" in text
        ), f"{name} should compare statuses case-insensitively across API versions"


def test_status_matching_uses_values_the_api_reports():
    """Prettified labels are a display concern; the state still carries these words."""
    text = (BLUEPRINT_DIR / "job_failed.yaml").read_text(encoding="utf-8")

    assert "'failed'" in text and "'warning'" in text


def test_readme_links_every_blueprint():
    """A blueprint nobody can find is a blueprint nobody uses."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    for path in blueprint_files():
        assert path.name in readme, f"{path.name} is not mentioned in the README"
        assert "blueprint_url" in readme, "README should offer one-click import links"


def test_hacs_declares_the_supported_home_assistant_version():
    """HACS blocks installation on older cores using this value."""
    hacs = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))

    major, minor = hacs["homeassistant"].split(".")[:2]
    assert (int(major), int(minor)) >= (2026, 1), (
        "blueprints use the plural trigger/action keys, which need 2024.10+; the project "
        "targets 2026.1+"
    )


def test_state_based_blueprints_survive_a_reload():
    """A reload takes every entity through unavailable, which looks like a state change.

    Without a guard, reloading the integration or restarting Home Assistant re-notifies about
    conditions that were already true and already reported.
    """
    text = (BLUEPRINT_DIR / "job_failed.yaml").read_text(encoding="utf-8")

    assert "trigger.from_state is not none" in text, "should ignore restored states"
    assert "unavailable" in text, "should name the state it is guarding against"


def test_numeric_blueprints_require_numeric_states():
    """ "unavailable" parsed as a number would read as zero licenses total."""
    text = (BLUEPRINT_DIR / "license_usage_high.yaml").read_text(encoding="utf-8")

    assert "int(-1)" in text, "an unreadable state should not be treated as a real number"
    assert "total | int > 0" in text, "a total of zero must not read as 100% used"


def test_state_triggers_that_pin_from_are_left_alone():
    """repository_offline is already safe: "unavailable -> on" cannot match "off -> on"."""
    text = (BLUEPRINT_DIR / "repository_offline.yaml").read_text(encoding="utf-8")

    assert 'from: "off"' in text and 'to: "on"' in text
    assert 'from: "on"' in text and 'to: "off"' in text


# Blueprints whose binary sensor triggers must never match a trip through unavailable
PINNED_BINARY_BLUEPRINTS = (
    "repository_offline.yaml",
    "proxy_offline.yaml",
    "server_health.yaml",
    "organization_sync_failed.yaml",
)


@pytest.mark.parametrize("name", PINNED_BINARY_BLUEPRINTS)
def test_binary_sensor_triggers_pin_from_and_to(name):
    """A reload goes on -> unavailable -> on; only a trigger pinned both ways ignores that."""
    document = load(BLUEPRINT_DIR / name)

    for entry in document["triggers"]:
        if entry["trigger"] != "state":
            continue
        assert entry.get("from") in ("on", "off"), f"{entry} should pin from"
        assert entry.get("to") in ("on", "off"), f"{entry} should pin to"
        assert entry["from"] != entry["to"]


@pytest.mark.parametrize(
    "name",
    ("repository_offline.yaml", "proxy_offline.yaml", "server_health.yaml"),
)
def test_recovery_only_follows_a_reported_problem(name):
    """A blip shorter than the delay raised no alert, so it must not raise a recovery."""
    text = (BLUEPRINT_DIR / name).read_text(encoding="utf-8")

    assert "trigger.from_state.last_changed" in text


def test_job_failed_listens_to_the_integrations_session_event():
    """The event name is the integration's; a typo would silently never trigger."""
    const = (REPO / "custom_components" / "veeam_365" / "const.py").read_text(encoding="utf-8")
    document = load(BLUEPRINT_DIR / "job_failed.yaml")

    event_types = {
        entry["event_type"] for entry in document["triggers"] if entry["trigger"] == "event"
    }
    assert event_types == {"veeam_365_job_session"}
    assert 'EVENT_JOB_SESSION = "veeam_365_job_session"' in const


def test_job_failed_keeps_a_status_trigger_for_servers_without_the_event_feed():
    """The event feed is API v8 only, and can drop out; Last Status works everywhere."""
    document = load(BLUEPRINT_DIR / "job_failed.yaml")

    platforms = {entry["trigger"] for entry in document["triggers"]}
    assert platforms == {"state", "event"}


# Inputs of the released blueprints. Removing or renaming one breaks every automation saved
# from the earlier version, so new behaviour has to come as new inputs with defaults.
RELEASED_INPUTS = {
    "job_failed.yaml": {"job_status_sensors", "include_warnings", "notification_action"},
    "daily_backup_summary.yaml": {
        "job_status_sensors",
        "report_time",
        "only_when_problems",
        "notification_action",
    },
    "repository_offline.yaml": {
        "repository_sensors",
        "offline_for",
        "notification_action",
        "recovery_action",
    },
    "license_expiring.yaml": {
        "expiry_sensors",
        "days_before",
        "check_time",
        "notification_action",
    },
    "license_usage_high.yaml": {
        "used_sensor",
        "total_sensor",
        "threshold",
        "notification_action",
        "recovery_action",
    },
}


@pytest.mark.parametrize("name", sorted(RELEASED_INPUTS))
def test_released_inputs_are_kept(name):
    declared = set(load(BLUEPRINT_DIR / name)["blueprint"]["input"])

    missing = RELEASED_INPUTS[name] - declared
    assert not missing, f"removing {sorted(missing)} breaks saved automations"


@pytest.mark.parametrize(
    "name",
    (
        "job_failed.yaml",
        "daily_backup_summary.yaml",
        "repository_offline.yaml",
        "proxy_offline.yaml",
        "server_health.yaml",
        "organization_not_backed_up.yaml",
        "organization_sync_failed.yaml",
    ),
)
def test_device_name_prefix_is_left_out_of_messages(name):
    """Devices are named "VB365 <Kind> <name>"; "Veeam 365 job VB365 Job Daily Mail" reads badly."""
    text = (BLUEPRINT_DIR / name).read_text(encoding="utf-8")

    assert "regex_replace('^VB365 " in text
