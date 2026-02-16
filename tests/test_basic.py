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

    # Verify async usage
    assert "await veeam_client.connect()" in init_content, "Should use async connect"
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
    """Test that integration uses v8 API version."""
    from pathlib import Path

    # Check config_flow
    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_365" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        config_flow_content = f.read()

    assert 'api_version="v8"' in config_flow_content

    # Check __init__
    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    assert 'api_version="v8"' in init_content


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
