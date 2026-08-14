<h1 align="center">
<br>
<img src="https://raw.githubusercontent.com/Cenvora/ha-veeam-365/main/media/Veeam_logo_2024_RGB_main_20.png"
     alt="Veeam Logo"
     height="100">
<br>
<br>
Veeam Backup for Microsoft 365 Integration for Home Assistant
</h1>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that monitors Veeam Backup for Microsoft 365 servers. This integration provides real-time monitoring of backup jobs and their status directly in Home Assistant. 

This project is an independent, open source Python client for the Veeam Backup for Microsoft 365 <a href="https://helpcenter.veeam.com/references/vbo365/8/rest/tag/SectionAbout">REST API</a>. It is not affiliated with, endorsed by, or sponsored by Veeam Software.

## Features

- 🔧 **UI Configuration Flow**: Easy setup through Home Assistant's UI
- 📊 **Job Monitoring**: Track all backup jobs and their current status
- 🔄 **Automatic Updates**: Polls the Veeam server every 60 seconds
- 🧭 **API Version Detection**: Finds the newest API version your server serves, and keeps up with it
- 🎨 **Dynamic Icons**: Visual indicators based on job status (success, running, failed, warning)
- 🏷️ **Readable Labels**: `NotConfigured` reads as "Not configured", with the raw value kept for automations
- 📱 **Rich Attributes**: Detailed information including last run, next run, and job type
- 🧹 **Device Cleanup**: Jobs and repositories deleted in Veeam can be removed from Home Assistant

## Requirements

- Home Assistant 2024.10.0 or newer
- Veeam Backup for Microsoft 365 server with REST API enabled (Community Edition not supported)

## Installation

> **Note**: The required `veeam-365` Python library is automatically installed by Home Assistant when you add this integration. No manual package installation is needed.

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/Cenvora/ha-veeam-365`
6. Select category: "Integration"
7. Click "Add"
8. Click "Install" on the Veeam Backup for Microsoft 365 card
9. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/veeam_365` directory to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

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
   - **API Version**: Leave on `auto` unless you have a reason not to (see below)
5. Click **Submit**

### API version

The REST API carries its version in every path — `/v8/Jobs` — and nothing negotiates one for
you, so a version has to be chosen up front.

Leaving the option on **auto** lets the integration find it. Every version this integration
supports is probed at once, and the newest one the server answers on is used. Detection needs
no credentials, costs about one round trip, and falls back to the newest packaged version if
nothing answers — a server behind a proxy that rewrites statuses is not a setup failure.

`auto` is stored as-is rather than resolved once, so it is re-evaluated on every restart or
reload: upgrading VB365, or updating the `veeam-365` library, moves the entry onto the newer
version by itself.

> [!NOTE]
> That is a trade. A newer API version can rename enum values and add fields, and `auto`
> adopts it on the next restart. Pin a version in the integration's options if you would
> rather adopt those deliberately.

If the connection fails, the configured port is checked against the port the REST API actually
answers on, and the error says so instead of a bare "cannot connect" — the service listens on
4443 out of the box, but the port is configurable in the console.

## Sensor values

Veeam reports enum values as identifiers: `EntireOrganization`, `NotConfigured`,
`AmazonS3Glacier`. Sensors show these as **Entire organization**, **Not configured** and
**Amazon S3 Glacier**.

Every prettified sensor also exposes the untouched API value as a `raw_value` attribute, so
automations and templates that need to match exactly have something stable to match on:

```jinja
{{ state_attr('sensor.nightly_backup_last_status', 'raw_value') == 'NotConfigured' }}
```

## Binary sensors

On/off states — server **Connected** and **Health OK**, repository **Online**, **Out of Date**,
**Immutable** and **Accessible**, and license **Auto Update Enabled** — are `binary_sensor`
entities, so Home Assistant renders them as Connected/Disconnected and OK/Problem rather than
`on`/`off`.

> [!IMPORTANT]
> These entities previously lived in the `sensor` domain. Upgrading moves them: `sensor.*`
> becomes `binary_sensor.*`, with history and settings preserved (the unique IDs are
> unchanged), and the old entity is removed rather than left behind as unavailable. Any
> automation, template or dashboard referring to the old `sensor.` entity IDs needs updating.

## Removing devices

A job or repository deleted in Veeam disappears from Home Assistant on the next poll. If the
server stops reporting an object while other objects of the same kind are still reported, its
device is removed automatically.

When nothing of that kind is reported at all — which is what a failed fetch looks like too —
nothing is pruned, and the device gets a **Delete** button instead. Deleting a device the
server still reports is refused, because the next poll would simply recreate it.

## Entities

The integration creates sensor entities for each backup job:

### Sensor Entity

- **Entity ID**: `sensor.veeam_<job_name>`
- **State**: Current job status (`success`, `running`, `failed`, `warning`, `unknown`)
- **Attributes**:
  - `job_id`: Unique job identifier
  - `job_name`: Display name of the job
  - `job_type`: Type of backup job
  - `last_run`: Timestamp of the last job execution
  - `next_run`: Timestamp of the next scheduled run
  - `last_result`: Result of the last job execution

## Automation Blueprints

Ready-made automations for the entities this integration creates. Each one asks you to pick
the entities to watch and what to do about it — a notification, a script, anything Home
Assistant can run — so they work with whatever notifier you already use.

Click **Import blueprint**, then create automations from it under
**Settings → Automations & scenes → Blueprints**.

> [!NOTE]
> Blueprints are not installed by HACS — Home Assistant has no mechanism for an integration to
> ship them, and HACS has no blueprint category. The import links below fetch them from this
> repository directly.

### Backup job failed

Notifies when a job's **Last Status** turns Failed (optionally Warning too). Works for backup
jobs and backup copy jobs alike.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Fjob_failed.yaml)

<sub>Source: [`job_failed.yaml`](blueprints/automation/veeam_365/job_failed.yaml)</sub>

### Daily backup summary

One digest a day: how many jobs succeeded, warned or failed, and which need attention.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Fdaily_backup_summary.yaml)

<sub>Source: [`daily_backup_summary.yaml`](blueprints/automation/veeam_365/daily_backup_summary.yaml)</sub>

### Repository offline

Fires when a backup repository stops being reachable, with an optional recovery notification.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Frepository_offline.yaml)

<sub>Source: [`repository_offline.yaml`](blueprints/automation/veeam_365/repository_offline.yaml)</sub>

### License expiring soon

Daily reminder once a license or its grace period is within N days of expiring.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Flicense_expiring.yaml)

<sub>Source: [`license_expiring.yaml`](blueprints/automation/veeam_365/license_expiring.yaml)</sub>

### Running out of licenses

VB365 licenses per protected user and picks up new users automatically, so a tenant can grow
past what is licensed. Fires when usage crosses a percentage of the licensed total.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Flicense_usage_high.yaml)

<sub>Source: [`license_usage_high.yaml`](blueprints/automation/veeam_365/license_usage_high.yaml)</sub>

## Support

- **Issues**: [GitHub Issues](https://github.com/Cenvora/ha-veeam-br/issues)
- **Documentation**: This README and inline code documentation

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Development Setup

To set up the development environment:

```bash
# Install development dependencies
pip install black isort flake8 mypy pre-commit

# Install pre-commit hooks (optional but recommended)
pre-commit install
```

### Code Quality

This project uses automated testing and formatting:

- **Black**: Code formatting (line length: 100)
- **isort**: Import sorting
- **flake8**: Linting
- **mypy**: Type checking
- **HACS Action**: HACS integration validation
- **Hassfest**: Home Assistant manifest validation

Run formatting and checks locally:

```bash
# Format code
black custom_components/
isort custom_components/

# Run linting
flake8 custom_components/

# Type checking
mypy custom_components/ --ignore-missing-imports

# Validate JSON
python -m json.tool custom_components/veeam_365/manifest.json
```

### CI/CD

All pull requests are automatically validated with:
- Python code formatting (Black, isort)
- Linting (flake8)
- Type checking (mypy)
- HACS validation
- Home Assistant manifest validation (hassfest)
- JSON schema validation

## License

This project is licensed under the terms included in the LICENSE file.

## Credits

This integration uses the [veeam-365](https://github.com/Cenvora/veeam-365) Python library for communication with Veeam Backup for Microsoft 365 servers. The library is automatically installed by Home Assistant when you add this integration - no manual installation required.
