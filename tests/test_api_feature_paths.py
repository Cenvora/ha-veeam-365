"""Test API feature path correctness."""


def test_sensor_api_feature_paths():
    """Test that sensor.py uses correct API feature paths."""
    from pathlib import Path

    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "sensor.py"

    with open(sensor_path) as f:
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


def test_button_api_feature_paths():
    """Test that button.py uses correct API feature paths."""
    from pathlib import Path

    button_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "button.py"

    with open(button_path) as f:
        button_content = f.read()

    # Verify correct API module names are used (singular form)
    assert 'check_api_feature_availability(api_version, "api.job")' in button_content, (
        "Jobs should use 'api.job' not 'api.jobs'"
    )

    # Verify incorrect names are NOT used
    assert 'check_api_feature_availability(api_version, "api.jobs")' not in button_content, (
        "Should not use plural 'api.jobs'"
    )


def test_const_api_feature_requirements():
    """Test that const.py documents correct API feature paths."""
    from pathlib import Path

    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "const.py"

    with open(const_path) as f:
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


def test_api_module_name_consistency():
    """Test that API module names are consistent between __init__.py and feature checks."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_365" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Verify __init__.py uses singular API module names
    assert 'veeam_client.api, "job"' in init_content, (
        "__init__.py should use singular 'job'"
    )
    assert 'veeam_client.api, "copy_job"' in init_content, (
        "__init__.py should use singular 'copy_job'"
    )
    # Note: backup_repository uses getattr, not api() method
    assert 'getattr(veeam_client, "backup_repository"' in init_content, (
        "__init__.py should reference 'backup_repository'"
    )
