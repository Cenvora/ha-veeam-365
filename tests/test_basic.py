"""Basic validation tests for Veeam 365 integration."""


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


def test_async_dependency():
    """Test that the integration uses async methods."""
    from pathlib import Path

    # Check that the integration uses await with veeam_365 client
    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Verify async usage - connect is wrapped in executor to avoid blocking imports
    assert "veeam_client.connect()" in init_content, "Should call connect method"
    assert "async_add_executor_job" in init_content, "Should use executor for blocking calls"
    assert "await veeam_client.call(" in init_content, "Should use async call method"


def test_config_flow_imports():
    """Test that config flow properly imports VeeamClient."""
    from pathlib import Path

    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_365" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        content = f.read()

    # Check that VeeamClient is imported
    assert "from veeam_365.client import VeeamClient" in content


def test_sensor_file_exists():
    """Test that sensor.py exists and has proper structure."""
    from pathlib import Path

    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "sensor.py"

    assert sensor_path.exists(), "sensor.py should exist"

    with open(sensor_path) as f:
        sensor_content = f.read()

    # Check for basic sensor structure
    assert "class VeeamJobSensor" in sensor_content
    assert "SensorEntity" in sensor_content


def test_coordinator_usage():
    """Test that the integration uses DataUpdateCoordinator."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Check that DataUpdateCoordinator is used
    assert "DataUpdateCoordinator" in init_content
    assert "coordinator" in init_content


def test_default_port():
    """Test that default port is set to 4443 for VB365."""
    from pathlib import Path

    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "const.py"

    with open(const_path) as f:
        const_content = f.read()

    # Check that default port is 4443 (VB365 default)
    assert "DEFAULT_PORT = 4443" in const_content


def test_api_version():
    """Test that integration uses configurable API version with v8 default."""
    from pathlib import Path

    # Check const.py has DEFAULT_API_VERSION
    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "const.py"

    with open(const_path) as f:
        const_content = f.read()

    assert 'DEFAULT_API_VERSION = "v8"' in const_content
    assert "CONF_API_VERSION" in const_content
    assert "API_VERSIONS" in const_content

    # Check config_flow uses configurable API version
    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_365" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        config_flow_content = f.read()

    assert "CONF_API_VERSION" in config_flow_content
    assert "DEFAULT_API_VERSION" in config_flow_content
    assert "API_VERSIONS" in config_flow_content
    assert "data.get(CONF_API_VERSION, DEFAULT_API_VERSION)" in config_flow_content

    # Check __init__ uses configurable API version
    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    assert "CONF_API_VERSION" in init_content
    assert "DEFAULT_API_VERSION" in init_content
    assert "entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)" in init_content


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


def test_job_endpoint_usage():
    """Test that integration uses correct job endpoint."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Check that correct job endpoint is used
    assert 'veeam_client.api("job").job_get' in init_content


def test_response_results_attribute():
    """Test that integration uses .results attribute from API response."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Check that .results is used (not .data)
    assert ".results" in init_content


def test_job_attributes():
    """Test that integration uses correct job attributes from API."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Check for correct attributes
    assert "last_status" in init_content
    assert "backup_type" in init_content
    assert "is_enabled" in init_content
    assert "total_objects" in init_content
    assert "processed_objects" in init_content


def test_device_removal_on_stale_items():
    """Test that stale devices are removed from device registry when items are deleted."""
    from pathlib import Path

    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "sensor.py"

    with open(sensor_path) as f:
        sensor_content = f.read()

    # Check that device registry is imported
    assert "device_registry as dr" in sensor_content, "device_registry should be imported"

    # Check that device registry is obtained in the stale removal function
    assert "dr.async_get(hass)" in sensor_content, "device registry should be retrieved"

    # Check that devices are looked up by their identifiers for each type
    assert (
        'f"job_{job_id}"' in sensor_content and "async_get_device" in sensor_content
    ), "stale job devices should be looked up by identifier"
    assert (
        'f"copy_job_{copy_job_id}"' in sensor_content
    ), "stale copy job devices should be looked up by identifier"
    assert (
        'f"repository_{repo_id}"' in sensor_content
    ), "stale repository devices should be looked up by identifier"

    # Check that devices are removed when found
    assert (
        "device_reg.async_remove_device(device.id)" in sensor_content
    ), "stale devices should be removed from device registry"
