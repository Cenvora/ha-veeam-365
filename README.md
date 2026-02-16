<h1 align="center">
<br>
Veeam Backup for Microsoft 365 Integration for Home Assistant
</h1>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that monitors Veeam Backup for Microsoft 365 servers. This integration provides real-time monitoring of backup jobs and their status directly in Home Assistant.

This project is an independent, open source project. It is not affiliated with, endorsed by, or sponsored by Veeam Software.

## Features

- 🔧 **UI Configuration Flow**: Easy setup through Home Assistant's UI
- 📊 **Job Monitoring**: Track all backup jobs and their current status
- 🏢 **Organization Monitoring**: Monitor Microsoft 365 organizations being backed up
- 💾 **Repository Monitoring**: View backup repository information
- 📜 **License Monitoring**: Track license status and usage
- 🔄 **Automatic Updates**: Polls the Veeam server every 60 seconds
- 🎨 **Dynamic Icons**: Visual indicators based on job status
- 📱 **Rich Attributes**: Detailed information including last run, next run, and job status
- 🔘 **Job Control Buttons**: Start, stop, and retry backup jobs

## Requirements

- Home Assistant 2023.1.0 or newer
- Veeam Backup for Microsoft 365 server with REST API enabled

## Installation

### HACS (Recommended)

Have [HACS](https://hacs.xyz/) installed, this will allow you to update easily.

* Adding ha-veeam-365 to HACS can be using this button:

[![image](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Cenvora&repository=ha-veeam-365&category=integration)

> [!NOTE]
> If the button above doesn't work, add `https://github.com/Cenvora/ha-veeam-365` as a custom repository of type Integration in HACS.

* Click install on the `Veeam Backup for Microsoft 365` integration.
* Restart Home Assistant.

<details><summary>Manual Install</summary>

* Copy the `custom_components/veeam_365` folder from this repository to the [`custom_components` folder](https://developers.home-assistant.io/docs/creating_integration_file_structure/#where-home-assistant-looks-for-integrations) in your config directory.
* Restart Home Assistant.
</details>

## Configuration

### Configuration Parameters

The integration supports the following configuration options:

#### Required Parameters
- **Host**: Your Veeam Backup for Microsoft 365 server hostname or IP address
- **Port**: REST API port (default: 4443)
- **Username**: Account with administrator privileges on the Veeam server
- **Password**: Password for the specified user account

#### Optional Parameters
- **Verify SSL**: Enable/disable SSL certificate verification (default: enabled)
  - Disable only if using self-signed certificates in a trusted environment
- **API Version**: Select the Veeam REST API version to use (configured via integration options)

### Via UI (Recommended)

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "Veeam Backup for Microsoft 365"
4. Enter your Veeam server details:
   - **Host**: Your Veeam server hostname or IP address
   - **Port**: REST API port (default: 4443)
   - **Username**: Veeam server username
   - **Password**: Veeam server password
   - **Verify SSL**: Whether to verify SSL certificates (recommended: enabled)
   - **API Version**: Select API version (default: 8)
5. Click **Submit**

### Reconfiguration

To update the integration settings:

1. Go to **Settings** → **Devices & Services**
2. Find the **Veeam Backup for Microsoft 365** integration
3. Click the three dots menu (⋮) and select **Reconfigure**
4. Update any settings as needed
5. Click **Submit**

### Re-authentication

If credentials expire or change:

1. Home Assistant will automatically prompt for re-authentication
2. Enter the new **Username** and **Password**
3. Click **Submit**

The integration will reconnect without losing any device or entity configurations.

## Data Updates

The integration polls the Veeam Backup for Microsoft 365 server every **60 seconds** to retrieve:
- Job status and statistics
- Organization information
- Repository information
- License details and expiration

**Update Behavior:**
- **New jobs/organizations**: Automatically detected and added as new devices
- **Status changes**: Reflected within the next polling cycle (60 seconds)
- **Failed connections**: Integration marks entities as unavailable and logs the error
- **Connection recovery**: Entities automatically become available when connection restored

## Entities

The integration creates devices for each monitored object (jobs, organizations, repositories, license), with multiple sensor entities per device:

### Job Devices

Each backup job creates a device with the following sensors:

- **Status Sensor**: `sensor.<job_name>_status`
  - State: Current job status (`success`, `running`, `failed`, `warning`, `unknown`)
- **Last Run Sensor**: `sensor.<job_name>_last_run`
  - State: Timestamp of the last job execution
- **Next Run Sensor**: `sensor.<job_name>_next_run`
  - State: Timestamp of the next scheduled run

### Job Control Buttons

Each backup job also has control buttons:

- **Start Button**: `button.<job_name>_start` - Start the backup job
- **Stop Button**: `button.<job_name>_stop` - Stop the running backup job
- **Retry Button**: `button.<job_name>_retry` - Retry the backup job

### Organization Devices

Each Microsoft 365 organization creates a device with:

- **Status Sensor**: `sensor.<org_name>_status`
  - State: Organization backup status (`enabled`, `disabled`)
  - Attributes: Organization ID, region, backup enabled status

### Repository Devices

Each backup repository creates a device with:

- **Type Sensor**: `sensor.<repo_name>_type`
  - State: Repository type
  - Attributes: Repository ID, description

### License Device

The license device has sensors for:

- **Status Sensor**: `sensor.license_status`
  - State: License status (`valid`, `expired`, `invalid`)
- **Expiration Sensor**: `sensor.license_expiration`
  - State: License expiration date
- **Users Sensor**: `sensor.license_users`
  - State: Number of used users
  - Attributes: Licensed users, used users

### Server Device

The server device has:

- **Last Poll Sensor**: `sensor.veeam_365_server_last_poll`
  - State: Timestamp of last successful API poll

## Example Automations

### Notify on Backup Failure

```yaml
automation:
  - alias: "Notify on Veeam 365 Backup Failure"
    trigger:
      - platform: state
        entity_id: sensor.my_backup_job_status
        to: "failed"
    action:
      - service: notify.notify
        data:
          title: "Veeam 365 Backup Failed"
          message: "Backup job {{ trigger.to_state.name | replace(' Status', '') }} has failed!"
```

### Daily Backup Status Report

```yaml
automation:
  - alias: "Daily Veeam 365 Status Report"
    trigger:
      - platform: time
        at: "08:00:00"
    action:
      - service: notify.notify
        data:
          title: "Veeam 365 Backup Status"
          message: >
            {% set ns = namespace(jobs=[]) %}
            {% for sensor in states.sensor %}
              {% if sensor.entity_id.endswith('_status') and device_attr(sensor.entity_id, 'manufacturer') == 'Veeam' and device_attr(sensor.entity_id, 'model') == 'Backup Job' %}
                {% set ns.jobs = ns.jobs + [sensor.name | replace(' Status', '') ~ ': ' ~ sensor.state] %}
              {% endif %}
            {% endfor %}
            {{ ns.jobs | join('\n') if ns.jobs else 'No Veeam 365 backup jobs found.' }}
```

## Removal

To remove the integration from Home Assistant:

1. Go to **Settings** → **Devices & Services**
2. Find the **Veeam Backup for Microsoft 365** integration
3. Click the three dots menu (⋮) and select **Delete**
4. Confirm the deletion

All devices and entities associated with this integration will be removed.

## Troubleshooting

### Connection Issues

**Problem**: Integration fails to connect to Veeam server

**Solutions**:
- Verify the Veeam server is running and accessible from Home Assistant
- Check that the REST API is enabled on the Veeam server
- Confirm the hostname/IP and port (default: 4443) are correct
- Ensure firewall rules allow traffic on port 4443
- Try disabling SSL verification if using self-signed certificates

### Authentication Failures

**Problem**: Invalid credentials error during setup or re-authentication

**Solutions**:
- Verify the username and password are correct
- Ensure the account has administrator privileges on the Veeam server
- Check if account is locked or password has expired
- Try logging in to the Veeam console with the same credentials

### Missing Entities

**Problem**: Some jobs or organizations don't appear as entities

**Solutions**:
- Wait for the next polling cycle (60 seconds)
- Restart Home Assistant to force a full refresh
- Check the Home Assistant logs for API errors
- Verify the jobs/organizations exist in Veeam console

### Entities Unavailable

**Problem**: Entities show as "unavailable"

**Solutions**:
- Check network connectivity to the Veeam server
- Review Home Assistant logs for connection errors
- Verify the Veeam server and REST API are running
- Try re-authenticating the integration

## Known Limitations

- **API Version Compatibility**: Requires Veeam Backup for Microsoft 365 v6 or newer
- **Stale Devices**: Deleted jobs/organizations remain as devices until manual removal
- **Real-time Updates**: Changes reflected every 60 seconds, not immediately
- **SSL Certificates**: Self-signed certificates require SSL verification to be disabled

## Support

- **Issues**: [GitHub Issues](https://github.com/Cenvora/ha-veeam-365/issues)
- **Documentation**: This README and inline code documentation

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the terms included in the LICENSE file.

## Credits

This integration uses the [veeam-365](https://github.com/Cenvora/veeam-365) Python library for communication with Veeam Backup for Microsoft 365 servers.

This integration is modeled after the [ha-veeam-br](https://github.com/Cenvora/ha-veeam-br) integration for Veeam Backup & Replication.

## 🤝 Core Contributors

This project is made possible thanks to the efforts of our core contributors:

- [Jonah May](https://github.com/JonahMMay)
- [Maurice Kevenaar](https://github.com/mkevenaar)
