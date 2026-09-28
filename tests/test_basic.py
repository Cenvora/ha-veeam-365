"""Basic validation tests for Veeam 365 integration."""

import json
from pathlib import Path
import re

import pytest


def test_manifest_valid():
    """Test that manifest.json is valid and contains required fields."""
    import json
    from pathlib import Path

    manifest_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_365" / "manifest.json"
    )

    with open(manifest_path) as f:
        manifest = json.load(f)

    # Check required fields
    required_fields = [
        "domain",
        "name",
        "version",
        "documentation",
        "requirements",
        "codeowners",
        "iot_class",
        "config_flow",
    ]
    for field in required_fields:
        assert field in manifest, f"Missing required field: {field}"

    # Check specific values
    assert manifest["domain"] == "veeam_365"
    assert manifest["config_flow"] is True
    assert "veeam-365" in manifest["requirements"][0]
    # 0.4.1 brought the exceptions module and the session recovery this integration uses
    assert ">=0.4.1" in manifest["requirements"][0]


def test_strings_valid():
    """Test that strings.json is valid."""
    import json
    from pathlib import Path

    strings_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "strings.json"

    with open(strings_path) as f:
        strings = json.load(f)

    # Check for required sections
    assert "config" in strings
    assert "step" in strings["config"]
    assert "user" in strings["config"]["step"]

    # Check for error and abort sections
    assert "error" in strings["config"]
    assert "abort" in strings["config"]


def test_imports():
    """Test that all modules can be imported."""
    from pathlib import Path

    # Check that key files exist
    base_path = Path(__file__).parent.parent / "custom_components" / "veeam_365"

    assert (base_path / "const.py").exists(), "const.py should exist"
    assert (base_path / "config_flow.py").exists(), "config_flow.py should exist"
    assert (base_path / "__init__.py").exists(), "__init__.py should exist"
    assert (base_path / "sensor.py").exists(), "sensor.py should exist"


def test_const_domain():
    """Test that DOMAIN constant is properly configured."""
    from pathlib import Path

    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "const.py"

    with open(const_path) as f:
        const_content = f.read()

    # Check that DOMAIN is defined correctly
    assert 'DOMAIN = "veeam_365"' in const_content


def test_default_port():
    """Test that default port is set to 4443 for VB365."""
    from pathlib import Path

    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "const.py"

    with open(const_path) as f:
        const_content = f.read()

    # Check that default port is 4443 (VB365 default)
    assert "DEFAULT_PORT = 4443" in const_content


def test_translation_files_exist():
    """Test that translation files exist for multiple languages."""
    from pathlib import Path

    translations_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_365" / "translations"
    )

    # Check that translations directory exists
    assert translations_path.exists(), "translations directory should exist"

    # Check for English translation
    en_path = translations_path / "en.json"
    assert en_path.exists(), "English translation should exist"

    # Check for other language translations
    expected_languages = ["cs", "de", "es", "fr", "it", "nl", "pl", "pt", "ru", "zh-Hans"]
    for lang in expected_languages:
        lang_file = translations_path / f"{lang}.json"
        assert lang_file.exists(), f"{lang} translation should exist"


COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_365"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}{key}.")
    else:
        yield prefix[:-1], node


def test_english_translation_is_strings_json():
    strings = (COMPONENT / "strings.json").read_text(encoding="utf-8")
    english = (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")

    assert json.loads(strings) == json.loads(english)


@pytest.mark.parametrize(
    "language", ["cs", "de", "es", "fr", "it", "nl", "pl", "pt", "ru", "zh-Hans"]
)
def test_translations_match_strings_json(language):
    """A missing key shows raw in the UI; a lost placeholder renders broken."""
    strings = dict(_flatten(json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))))
    translated = dict(
        _flatten(
            json.loads(
                (COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8")
            )
        )
    )

    assert set(translated) == set(strings)
    for key, text in strings.items():
        assert set(PLACEHOLDER.findall(translated[key])) == set(
            PLACEHOLDER.findall(text)
        ), f"{language}: {key}"


def test_every_translation_key_in_code_exists():
    """Entities and buttons name translation keys; each must be in strings.json."""
    from custom_components.veeam_365 import binary_sensor, button, sensor

    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    entity = strings["entity"]

    for description in (
        *sensor.JOB_SENSORS,
        *sensor.COPY_JOB_SENSORS,
        *sensor.REPOSITORY_SENSORS,
        *sensor.PROXY_SENSORS,
        *sensor.REPOSITORY_MAINTENANCE_SENSORS,
        *sensor.ORGANIZATION_SENSORS,
        *sensor.ORGANIZATION_SYNC_SENSORS,
        *sensor.ORGANIZATION_SYNC_PROGRESS_SENSORS,
        *sensor.SERVER_SENSORS,
        *sensor.LICENSE_SENSORS,
    ):
        assert description.translation_key in entity["sensor"], description.translation_key
    from custom_components.veeam_365.coordinator import PROTECTED_OPERATIONS

    for kind in PROTECTED_OPERATIONS:
        assert f"organization_protected_{kind}" in entity["sensor"], kind
        assert kind in sensor.PROTECTED_ICONS, kind
    for description in (
        *binary_sensor.REPOSITORY_BINARY_SENSORS,
        *binary_sensor.PROXY_BINARY_SENSORS,
        *binary_sensor.REPOSITORY_MAINTENANCE_BINARY_SENSORS,
        *binary_sensor.ORGANIZATION_BINARY_SENSORS,
        *binary_sensor.ORGANIZATION_SYNC_BINARY_SENSORS,
    ):
        assert description.translation_key in entity["binary_sensor"]
    for description in (
        *button.JOB_BUTTONS,
        *button.COPY_JOB_BUTTONS,
        *button.REPOSITORY_BUTTONS,
        *button.REPOSITORY_MAINTENANCE_BUTTONS,
        *button.ORGANIZATION_BUTTONS,
    ):
        assert description.translation_key in entity["button"], description.translation_key
        assert description.failure_key in strings["exceptions"], description.failure_key
        message = strings["exceptions"][description.failure_key]["message"]
        assert f"{{{description.name_placeholder}}}" in message
