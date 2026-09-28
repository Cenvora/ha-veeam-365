"""Tests for the binary sensor platform.

Home Assistant derives an entity domain from the platform that creates it, not from the entity
class. BinarySensorEntity subclasses added by the sensor platform therefore landed in the sensor
domain, where the binary-sensor device class wording never applies and every one of them
displayed as a raw "on"/"off".

In the binary_sensor domain the same entities read as Connected/Disconnected, OK/Problem and
Running/Not running, with no per-entity strings to maintain.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_365"
BINARY_PATH = COMPONENT / "binary_sensor.py"
SENSOR_PATH = COMPONENT / "sensor.py"
INIT_PATH = COMPONENT / "__init__.py"
BLUEPRINTS = Path(__file__).parent.parent / "blueprints" / "automation" / "veeam_365"


def binary_source():
    return BINARY_PATH.read_text(encoding="utf-8")


def test_the_platform_exists_and_is_registered():
    """Without the platform in PLATFORMS, nothing sets these entities up at all."""
    assert BINARY_PATH.exists()

    init = INIT_PATH.read_text(encoding="utf-8")
    assert "Platform.BINARY_SENSOR" in init


def test_no_binary_entities_are_left_on_the_sensor_platform():
    """A BinarySensorEntity created by the sensor platform is the original bug."""
    sensor = SENSOR_PATH.read_text(encoding="utf-8")

    assert "BinarySensorEntity" not in sensor
    assert "BinarySensorDeviceClass" not in sensor


def test_every_binary_sensor_moved():
    """Server health and connectivity, license auto-update, and the four repository ones."""
    from custom_components.veeam_365 import binary_sensor

    keys = {description.key for description in binary_sensor.REPOSITORY_BINARY_SENSORS}
    assert keys == {"online", "out_of_date", "immutable", "accessible"}
    for name in (
        "VeeamServerHealthOkSensor",
        "VeeamServerConnectedSensor",
        "VeeamLicenseAutoUpdateSensor",
    ):
        assert hasattr(binary_sensor, name)


@pytest.mark.parametrize(
    "key,device_class",
    [
        ("online", None),
        ("out_of_date", "problem"),
        ("immutable", None),
        ("accessible", "connectivity"),
    ],
)
def test_repository_device_classes(key, device_class):
    """The device class is what turns on/off into readable text.

    "online" never measured connectivity — it reports whether an object storage cache is
    in sync — so it lost the CONNECTIVITY class along with its misleading name.
    Immutability being off is a configuration choice, so PROBLEM would wrongly show red.
    """
    from custom_components.veeam_365 import binary_sensor

    (description,) = [d for d in binary_sensor.REPOSITORY_BINARY_SENSORS if d.key == key]
    assert (description.device_class or None) == device_class


@pytest.mark.parametrize(
    "entity,device_class",
    [
        ("VeeamServerHealthOkSensor", "running"),
        ("VeeamServerConnectedSensor", "connectivity"),
        ("VeeamLicenseAutoUpdateSensor", "update"),
    ],
)
def test_server_and_license_device_classes(entity, device_class):
    assert _instance(entity).device_class == device_class


def test_connectivity_sensors_can_report_off():
    """Unavailable is what these report *about*; they must be able to say off instead."""
    for entity in ("VeeamServerHealthOkSensor", "VeeamServerConnectedSensor"):
        sensor = _instance(entity)
        sensor.coordinator.last_update_success = False
        assert sensor.available is True
        assert sensor.is_on is False


def _instance(name):
    from custom_components.veeam_365 import binary_sensor

    entry = MagicMock(entry_id="entry-1", data={"host": "veeam.example.com"})
    return getattr(binary_sensor, name)(MagicMock(), entry)


def test_old_sensor_entities_are_cleaned_up_on_upgrade():
    """The unique IDs do not change, only the domain, so both would otherwise coexist and the
    stale one would sit there unavailable forever."""
    source = binary_source()

    assert "_drop_superseded_sensor_entities" in source
    block = source[source.index("def _drop_superseded_sensor_entities") :]
    block = block[: block.index("@dataclass")]

    assert 'existing.domain != "sensor"' in block, "only sensor-domain strays should be removed"
    assert "existing.unique_id not in unique_ids" in block, "matching should be by unique ID"
    assert "registry.async_remove" in block
    assert "_LOGGER.info" in block, "an entity id changing under the user deserves a log line"


def test_the_blueprints_can_now_find_these_entities():
    """The repository blueprint filters on domain: binary_sensor, which matched nothing while
    the entities lived in the sensor domain."""
    filters = 0
    for path in sorted(BLUEPRINTS.glob("*.yaml")):
        filters += path.read_text(encoding="utf-8").count("domain: binary_sensor")

    assert filters >= 1, "the repository blueprint depends on this domain"


def test_unique_ids_are_unchanged_by_the_move():
    """Changing them would strand every entity's history and customisations."""
    source = binary_source()

    for suffix in (
        '"server_health_ok"',
        '"server_connected"',
        '"service_health"',
        '"license_auto_update"',
        'key="online"',
        'key="out_of_date"',
        'key="immutable"',
        'key="accessible"',
    ):
        assert suffix in source, f"{suffix} unique ID suffix should be preserved"
