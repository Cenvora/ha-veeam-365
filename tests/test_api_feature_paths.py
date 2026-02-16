"""Test API feature path correctness."""

from pathlib import Path

import pytest


@pytest.fixture
def component_path():
    """Return the base path to the custom component."""
    return Path(__file__).parent.parent / "custom_components" / "veeam_365"


def test_sensor_api_feature_paths(component_path):
    """Test that sensor.py uses correct API feature paths."""
    sensor_path = component_path / "sensor.py"

    with open(sensor_path, encoding="utf-8") as f:
        sensor_content = f.read()

    # Verify correct API module names are used (singular form)
    assert 'check_api_feature_availability(api_version, "api.job")' in sensor_content, (
        "Jobs should use 'api.job' not 'api.jobs'"
    )
    assert 'check_api_feature_availability(api_version, "api.copy_job")' in sensor_content, (
        "Copy jobs should use 'api.copy_job' not 'api.copy_jobs'"
    )
    assert (
        'check_api_feature_availability(api_version, "api.backup_repository")' in sensor_content
    ), "Repositories should use 'api.backup_repository' not 'api.repositories'"

    # Verify incorrect names are NOT used
    assert 'check_api_feature_availability(api_version, "api.jobs")' not in sensor_content, (
        "Should not use plural 'api.jobs'"
    )
    assert 'check_api_feature_availability(api_version, "api.copy_jobs")' not in sensor_content, (
        "Should not use plural 'api.copy_jobs'"
    )
    assert (
        'check_api_feature_availability(api_version, "api.repositories")' not in sensor_content
    ), "Should not use plural 'api.repositories'"


def test_button_api_feature_paths(component_path):
    """Test that button.py uses correct API feature paths."""
    button_path = component_path / "button.py"

    with open(button_path, encoding="utf-8") as f:
        button_content = f.read()

    # Verify correct model names are used for jobs (action suffix, not spec)
    assert 'check_api_feature_availability(api_version, "models.job_start_action")' in button_content, (
        "Jobs should use 'models.job_start_action' not 'models.job_start_spec'"
    )
    assert 'check_api_feature_availability(api_version, "models.job_stop_action")' in button_content, (
        "Jobs should use 'models.job_stop_action' not 'models.job_stop_spec'"
    )
    assert 'check_api_feature_availability(api_version, "models.job_enable_action")' in button_content, (
        "Jobs should use 'models.job_enable_action'"
    )
    assert 'check_api_feature_availability(api_version, "models.job_disable_action")' in button_content, (
        "Jobs should use 'models.job_disable_action'"
    )

    # Verify copy job model names are used
    assert 'check_api_feature_availability(api_version, "models.copy_job_start_action")' in button_content, (
        "Copy jobs should use 'models.copy_job_start_action'"
    )
    assert 'check_api_feature_availability(api_version, "models.copy_job_stop_action")' in button_content, (
        "Copy jobs should use 'models.copy_job_stop_action'"
    )
    assert 'check_api_feature_availability(api_version, "models.copy_job_enable_action")' in button_content, (
        "Copy jobs should use 'models.copy_job_enable_action'"
    )
    assert 'check_api_feature_availability(api_version, "models.copy_job_disable_action")' in button_content, (
        "Copy jobs should use 'models.copy_job_disable_action'"
    )

    # Verify repository synchronize model is used
    assert 'check_api_feature_availability(api_version, "models.backup_repository_start_synchronize_action")' in button_content, (
        "Repository should use 'models.backup_repository_start_synchronize_action'"
    )

    # Verify incorrect names are NOT used (spec suffix instead of action)
    assert 'check_api_feature_availability(api_version, "models.job_start_spec")' not in button_content, (
        "Should not use 'models.job_start_spec', use 'models.job_start_action' instead"
    )
    assert 'check_api_feature_availability(api_version, "models.job_stop_spec")' not in button_content, (
        "Should not use 'models.job_stop_spec', use 'models.job_stop_action' instead"
    )

    # Verify we're using singular API module names in code
    assert 'veeam_client.api, "job"' in button_content, (
        "Should use singular 'job' not 'jobs' when calling veeam_client.api"
    )
    assert 'veeam_client.api, "copy_job"' in button_content, (
        "Should use singular 'copy_job' not 'copy_jobs' when calling veeam_client.api"
    )
    assert 'veeam_client.api, "backup_repository"' in button_content, (
        "Should use singular 'backup_repository' when calling veeam_client.api"
    )


def test_const_api_feature_requirements(component_path):
    """Test that const.py documents correct API feature paths."""
    const_path = component_path / "const.py"

    with open(const_path, encoding="utf-8") as f:
        const_content = f.read()

    # Verify that API_FEATURE_REQUIREMENTS uses correct module names
    assert '"jobs_data": "api.job"' in const_content, (
        "jobs_data should reference 'api.job'"
    )
    assert '"copy_jobs_data": "api.copy_job"' in const_content, (
        "copy_jobs_data should reference 'api.copy_job'"
    )
    assert '"repositories_data": "api.backup_repository"' in const_content, (
        "repositories_data should reference 'api.backup_repository'"
    )


def test_api_module_name_consistency(component_path):
    """Test that API module names are consistent between __init__.py and feature checks."""
    init_path = component_path / "__init__.py"

    with open(init_path, encoding="utf-8") as f:
        init_content = f.read()

    # Verify __init__.py uses singular API module names
    assert 'veeam_client.api, "job"' in init_content, (
        "__init__.py should use singular 'job'"
    )
    assert 'veeam_client.api, "copy_job"' in init_content, (
        "__init__.py should use singular 'copy_job'"
    )
    assert 'veeam_client.api, "backup_repository"' in init_content, (
        "__init__.py should use singular 'backup_repository'"
    )
