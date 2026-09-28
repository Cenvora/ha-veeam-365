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

- Home Assistant 2026.1 or newer
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

## Devices and entity IDs

Every device this integration creates is named with a **VB365** prefix, and the kind of
object it is:

| Device | Name |
| --- | --- |
| Server | `VB365 Server <host>` |
| License | `VB365 License <host>` |
| Backup job | `VB365 Job <job name>` |
| Backup copy job | `VB365 Copy Job <copy job name>` |
| Repository | `VB365 Repository <repository name>` |
| Backup proxy | `VB365 Proxy <host name>` |
| Microsoft 365 organization | `VB365 Organization <organization name>` |

The kind is left out when the name already says it, so a job called "Daily Mail Job" is
`VB365 Daily Mail Job`, not `VB365 Job Daily Mail Job`.

Entity IDs are built from the device name plus the entity name, so a new installation gets
IDs such as `sensor.vb365_job_daily_mail_last_status`, `sensor.vb365_license_status` and
`binary_sensor.vb365_server_veeam_example_com_connected`. The prefix keeps them apart from
the Veeam Backup & Replication integration, whose devices would otherwise also be called
"License" and "Server" — one of the two then ended up with IDs like
`sensor.veeam_license_status_2`.

> [!NOTE]
> Upgrading renames the devices, but **existing entity IDs are left alone**: Home Assistant
> keeps the IDs it already registered, so automations and dashboards keep working. To move
> an existing installation onto the new IDs, open **Settings → Devices & services →
> Entities**, select the Veeam entities and choose **Recreate entity IDs** from the
> selection menu where your Home Assistant version offers it — or open each device, rename
> it (keeping the new name is fine) and accept the offer to rename its entity IDs. Update
> anything that referred to the old IDs afterwards.

## Sensor values

Veeam reports enum values as identifiers: `EntireOrganization`, `NotConfigured`,
`AmazonS3Glacier`. Sensors show these as **Entire organization**, **Not configured** and
**Amazon S3 Glacier**.

Every prettified sensor also exposes the untouched API value as a `raw_value` attribute, so
automations and templates that need to match exactly have something stable to match on:

```jinja
{{ state_attr('sensor.vb365_job_nightly_backup_last_status', 'raw_value') == 'NotConfigured' }}
```

A value the server does not report — the last backup time on API v6, say, or a field that
comes back empty — reads as **unknown**, never as a placeholder string. A status newer than
the `veeam-365` library knows about is shown as the server sent it rather than dropping the
whole job.

## Binary sensors

On/off states are `binary_sensor` entities, so Home Assistant renders them as
Connected/Disconnected and OK/Problem rather than `on`/`off`:

- Server **Connected** — off while polls fail. It stays available, so it can actually say
  "Disconnected" instead of going unavailable exactly when it matters.
- Server **Health OK** — off while polls fail *or* any endpoint (jobs, copy jobs,
  repositories, proxies, organizations, organization sync, license, server info, health
  report) answers with an error; the `failed_endpoints` attribute says which. This is about
  whether the integration's polls get answers, not about the server's own health — that is
  Service Health.
- Server **Service Health** (API v8) — Problem when the server's own health report
  (`/v8/Health`) says Unhealthy. The report covers the NATS server and the PostgreSQL
  configuration database: the `checks` attribute holds each one's status and description,
  and `problems` lists the descriptions of the failing ones, ready for a notification.
- Repository **Accessible** — off when the server reports the repository as Invalid (API
  v8; unknown on older versions).
- Repository **Cache In Sync** — off when an object storage repository's local cache needs
  synchronizing. It used to be called **Online**, which it never measured; existing
  installations keep its `_online` entity ID.
- Repository **Out of Date** (API v8) and **Immutable**, and license **Auto Update Enabled**.
- Proxy **Online** — off when the server reports the backup proxy as Offline.
- Organization **Sync** (API v7 and later) — Problem when the organization's last cache
  synchronization with Microsoft 365 failed; the `error` attribute says why, and on API v8
  `parts` breaks it down into users, groups, group members and sites.

> [!IMPORTANT]
> These entities previously lived in the `sensor` domain. Upgrading moves them: `sensor.*`
> becomes `binary_sensor.*`, with history and settings preserved (the unique IDs are
> unchanged), and the old entity is removed rather than left behind as unavailable. Any
> automation, template or dashboard referring to the old `sensor.` entity IDs needs updating.

## When the server misbehaves

- **Unreachable or timing out** at startup: setup is retried automatically. During
  polling, entities go unavailable and **Connected** turns off until the server answers.
- **Credentials refused**: Home Assistant asks you to re-authenticate (Settings → Devices &
  services shows a **Reconfigure** prompt).
- **One endpoint failing** — the license endpoint refusing a restricted account, for
  instance: only that endpoint's entities go unavailable, and **Health OK** turns off. If
  the jobs endpoint fails, the whole update fails.
- **Buttons** report failure: when the server rejects Start, Stop, Enable, Disable or
  Synchronize, Home Assistant shows the server's error rather than pretending it worked.

## Removing devices

A job or repository deleted in Veeam disappears from Home Assistant on the next poll. If the
server stops reporting an object while other objects of the same kind are still reported, its
device is removed automatically.

When nothing of that kind is reported at all, or the fetch failed, nothing is pruned — both
would otherwise look like everything being deleted — and the device gets a **Delete** button
instead. Deleting a device the server still reports is refused, because the next poll would
simply recreate it.

## Entities

Per **backup job**: Last Status, Last Run, Next Run, Last Backup (API v7 and later), Backup
Type, Enabled and Name sensors, plus Start, Stop, Enable and Disable buttons.

Per **backup copy job**: Last Status, Last Run, Last Backup, Enabled and Name sensors, plus
Start, Stop, Enable and Disable buttons.

Per **repository**: Type, Description, Used Space (GiB; local repositories report capacity
minus free space, object storage repositories their used space) and, when immutability is
on, Immutability Days sensors; the binary sensors above; and a Synchronize Cache button.

Per **backup proxy**: an Online binary sensor (with the FQDN, port, roles and proxy pool as
attributes) on every API version. API v8 adds Maintenance Mode (Disabled, Enabling or
Enabled, with the untouched value as `raw_value`), CPU Usage and Memory Usage (%), Version
and Operating System sensors. An offline proxy reports no usage, so those read unknown until
it is back. Maintenance mode is read-only: VB365 needs the proxy host's own administrator or
SSH credentials to switch it, which this integration does not hold.

Per **Microsoft 365 organization**: Last Backup (with the first backup, the tenant's
Microsoft name and its protected services as attributes), Licensed Users, New Users, Type
and Region sensors, and a Backed Up binary sensor. From API v7 the organization's cache sync
adds the Sync binary sensor above, a Last Sync sensor and a **Synchronize** button, which
starts an incremental sync as the console does. API v8 adds a Sync Status sensor (Idle,
Queued or Running, with the next scheduled sync as `next_sync`). On v8 the sync state of
every organization comes in one request; v7 asks for each organization separately.

On the **server**: Product Version, Installation ID and Last Successful Poll sensors, and the
Connected, Health OK and (API v8) Service Health binary sensors.

On the **license**: Status, Type, Expiration Date, Grace Period Expiration, Licensed To,
Total/Used/New Licenses sensors, and the Auto Update Enabled binary sensor.

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

Fires when a backup repository stops being accessible (or its cache falls out of sync), with an
optional recovery notification.

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
