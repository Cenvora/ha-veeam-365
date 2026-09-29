<h1 align="center">
<br>
<img src="https://raw.githubusercontent.com/Cenvora/ha-veeam-365/main/custom_components/veeam_365/brand/logo.png"
     alt="Veeam Logo"
     height="100">
<br>
<br>
Veeam Backup for Microsoft 365 Integration for Home Assistant
</h1>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that monitors Veeam Backup for Microsoft 365 servers. This integration provides real-time monitoring of backup jobs and their status directly in Home Assistant. 

This project is an independent, open source project. It is not affiliated with, endorsed by, or sponsored by Veeam Software.

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
### HACS (Recommended)

Have [HACS](https://hacs.xyz/) installed, this will allow you to update easily.

* Adding ha-veeam-365 to HACS can be using this button:

[![image](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Cenvora&repository=ha-veeam-365&category=integration)

> [!NOTE]
> If the button above doesn't work, add `https://github.com/Cenvora/ha-veeam-365` as a custom repository of type Integration in HACS.

* Click install on the `Veeam Backup for M365` integration.
* Restart Home Assistant.

<details><summary>Manual Install</summary>

* Copy the `custom_components/veeam_365` folder from the [latest release](https://github.com/Cenvora/ha-veeam-365/releases/latest) to the [`custom_components` folder](https://developers.home-assistant.io/docs/creating_integration_file_structure/#where-home-assistant-looks-for-integrations) in your config directory.
* Restart Home Assistant.
</details>

The required `veeam-365` Python library is installed automatically by Home Assistant.

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
   - **API Version**: Leave on *Automatic* unless you have a reason not to (see below)
5. Click **Submit**

### API version

The REST API carries its version in every path — `/v8/Jobs` — and nothing negotiates one for
you, so a version has to be chosen up front.

Leaving the option on **Automatic** lets the integration find it. Every version this integration
supports is probed at once, and the newest one the server answers on is used. Detection needs
no credentials, costs about one round trip, and falls back to the newest packaged version if
nothing answers — a server behind a proxy that rewrites statuses is not a setup failure.

*Automatic* is stored as-is rather than resolved once, so it is re-evaluated on every restart or
reload: upgrading VB365, or updating the `veeam-365` library, moves the entry onto the newer
version by itself.

> [!NOTE]
> That is a trade. A newer API version can rename enum values and add fields, and *Automatic*
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
| Backup proxy pool (API v8) | `VB365 Proxy Pool <pool name>` |
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
- Server **Health OK** — off while polls fail *or* any endpoint (jobs, copy jobs, job
  sessions, repositories, repository maintenance, proxies, proxy pools, organizations,
  organization sync, license, server info, health report) answers with an error; the
  `failed_endpoints` attribute says which. This is about whether the integration's polls get answers, not about
  the server's own health — that is Service Health.
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
- Proxy pool **Online** (API v8) — off when none of the pool's proxies is online, so the
  pool has nothing left to process with. Proxy pool **Degraded** — Problem while any of its
  proxies is offline; `offline_proxies` names them.
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

On API v8 both also show their **latest session**: Last Session (when it started, with its
status, Full or Incremental, end time, details — the error message when it failed —, retries
and bottleneck as attributes), Last Session Duration (so far, while it runs), Last Session
Transferred, Last Session Processed Objects and Last Session Processing Rate. Sessions are
found without paging through history on every poll: each poll asks for the running sessions
and for those since the previous poll, and re-reads a session it last saw running once it
has finished. After a restart the first poll looks back a day, so a job that has not run
since reads unknown until its next run.

Per **repository**: Type, Description, Used Space (GiB; local repositories report capacity
minus free space, object storage repositories their used space) and, when immutability is
on, Immutability Days sensors; the binary sensors above; and a Synchronize Cache button.
On VB365 8.6 and later each repository also gets a **Maintenance** binary sensor (on while a
maintenance session suspends operations on it), a Maintenance Status sensor (the current or
latest session's status — Never, Running, Finished, Failed and so on — with its start and
end time and any error), and **Start Maintenance** / **Stop Maintenance** buttons. Start
waits up to an hour for the repository's running sessions to finish and is canceled if they
do not; it never force-stops a backup in progress. Stop ends the active session, and reports
it when there is none. Older 8.x servers serve API v8 without maintenance sessions, so there
the integration neither asks for them nor creates these entities; after upgrading a server
to 8.6, restart Home Assistant (or reload the integration) to get them.

Per **backup proxy**: an Online binary sensor (with the FQDN, port, roles and proxy pool as
attributes) on every API version. API v8 adds Maintenance Mode (Disabled, Enabling or
Enabled, with the untouched value as `raw_value`), CPU Usage and Memory Usage (%), Version
and Operating System sensors. An offline proxy reports no usage, so those read unknown until
it is back. Maintenance mode is read-only: VB365 needs the proxy host's own administrator or
SSH credentials to switch it, which this integration does not hold.

Per **backup proxy pool** (API v8): Proxies and Online Proxies sensors, and the Online and
Degraded binary sensors above. The server reports only a pool's name and description, so all
of these come from the proxies that name the pool: they go unavailable while the proxies
cannot be read, rather than counting from a stale list. The Proxies sensor lists the pool's
proxies and the repositories it serves as attributes. A pool with no proxies reads offline.

Per **Microsoft 365 organization**: Last Backup (with the first backup, the tenant's
Microsoft name and its protected services as attributes), Licensed Users, New Users, Type
and Region sensors, and a Backed Up binary sensor. From API v7 the organization's cache sync
adds the Sync binary sensor above, a Last Sync sensor and a **Synchronize** button, which
starts an incremental sync as the console does. API v8 adds a Sync Status sensor (Idle,
Queued or Running, with the next scheduled sync as `next_sync`). On v8 the sync state of
every organization comes in one request; v7 asks for each organization separately.

On API v8 each organization also gets **Protected Users**, **Protected Groups**, **Protected
Sites** and **Protected Teams** counts, with the time of the count as `counted_at`. VB365
reports no totals, so they are counted by paging through every protected object — hourly,
separately from the regular poll, so a large tenant can neither slow the other entities down
nor make them unavailable. The counts appear shortly after startup, and a kind that cannot
be counted goes unavailable while the others carry on.

On the **server**: Product Version, Installation ID and Last Successful Poll sensors, and the
Connected, Health OK and (API v8) Service Health binary sensors.

On the **license**: Status, Type, Expiration Date, Grace Period Expiration, Licensed To,
Total/Used/New Licenses sensors, and the Auto Update Enabled binary sensor.

## Live updates (API v8)

On API v8 the integration also follows VB365's event feed (`/v8/Events`), a request the
server holds open until something changes. Whenever a job or one of its sessions changes,
the entities are refreshed within seconds instead of on the next minute's poll, and the
session that changed is read directly rather than searched for.

When a job session's status changes, a `veeam_365_job_session` event is also fired on the
Home Assistant event bus, so an automation can react to it without watching a sensor:

```yaml
triggers:
  - trigger: event
    event_type: veeam_365_job_session
    event_data:
      status: Failed
actions:
  - action: notify.notify
    data:
      message: "VB365 job {{ trigger.event.data.job_name }} failed"
```

The event carries `entry_id`, `job_id`, `job_name`, `job_type` (Backup or Copy),
`session_id` and `status` (Running, Success, Warning, Failed, Stopped or NotConfigured).

The feed only speeds things up; the regular poll carries on regardless. If the feed fails
or the server does not have it, a warning is logged once, the integration retries with
increasing pauses (up to five minutes), and meanwhile everything updates on the poll as
before. Changes made while Home Assistant was not listening are picked up by the next poll.
The feed's state is in the integration's diagnostics.

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
jobs and backup copy jobs alike. On API v8 it also listens to the `veeam_365_job_session`
event, which catches a job failing again right after a previous failure (Last Status stays
Failed, so the sensor alone cannot show it); each failure is still reported once.

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

### Organization not backed up

Fires when a Microsoft 365 organization's **Last Backup** is older than N hours (26 by
default), and optionally as soon as its **Backed Up** sensor turns off. A job that is disabled
or stuck never fails, so this catches what the job alert cannot. Each organization is reported
once, with an optional notification when backups resume.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Forganization_not_backed_up.yaml)

<sub>Source: [`organization_not_backed_up.yaml`](blueprints/automation/veeam_365/organization_not_backed_up.yaml)</sub>

### Organization sync failed

Fires when an organization's **Sync** sensor reports that synchronizing its users, groups and
sites failed — new ones are not backed up until it succeeds — with the server's error message
and an optional recovery notification.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Forganization_sync_failed.yaml)

<sub>Source: [`organization_sync_failed.yaml`](blueprints/automation/veeam_365/organization_sync_failed.yaml)</sub>

### Backup proxy offline

Fires when a backup proxy's **Online** sensor stays off for a while, skipping proxies in
maintenance mode, with an optional recovery notification.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Fproxy_offline.yaml)

<sub>Source: [`proxy_offline.yaml`](blueprints/automation/veeam_365/proxy_offline.yaml)</sub>

### Server health problem

Fires when the server's **Health OK** sensor stays off for a few minutes (5 by default, so a
single slow poll does not count), naming the parts of the server that could not be read, and
optionally when its **Service Health** (API v8) reports Unhealthy. Sends a recovery
notification too, unless turned off.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Fserver_health.yaml)

<sub>Source: [`server_health.yaml`](blueprints/automation/veeam_365/server_health.yaml)</sub>

### License expiring soon

Daily reminder once a license or its grace period is within N days of expiring.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Flicense_expiring.yaml)

<sub>Source: [`license_expiring.yaml`](blueprints/automation/veeam_365/license_expiring.yaml)</sub>

### Running out of licenses

VB365 licenses per protected user and picks up new users automatically, so a tenant can grow
past what is licensed. Fires when usage crosses a percentage of the licensed total.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-365%2Fmain%2Fblueprints%2Fautomation%2Fveeam_365%2Flicense_usage_high.yaml)

<sub>Source: [`license_usage_high.yaml`](blueprints/automation/veeam_365/license_usage_high.yaml)</sub>

## Licensing

**Community Edition, and servers whose license cannot be read, are not supported.**
Entitlements differ, and some REST API endpoints answer differently or not at all, so entities
can be missing or unreliable in ways that look like integration bugs.

The integration reads the license it is already polling for and, if it finds an unsupported
one, raises a warning under **Settings → Repairs**. Nothing is blocked, and the warning clears
itself once the server reports a supported license.

A second repair appears when the license is within 30 days of expiring (a warning), and again
once it has expired (an error). Both are checked on every poll and clear on their own once the
server reports a renewed license.

## Removal

To remove the integration from Home Assistant:

1. Go to **Settings** → **Devices & Services**
2. Find the **Veeam Backup for M365** integration
3. Click the three dots menu (⋮) and select **Delete**
4. Confirm the deletion

All devices and entities associated with this integration will be removed.

## Support

- **Issues**: [GitHub Issues](https://github.com/Cenvora/ha-veeam-365/issues)
- **Documentation**: This README and inline code documentation

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Development Setup

To set up the development environment:

```bash
# Install development and test dependencies
pip install ruff ty -r requirements_test.txt
```

### Code Quality

This project uses automated testing and formatting:

- **Ruff**: Code formatting and linting (line length: 100)
- **ty**: Type checking
- **pytest**: Tests, using `pytest-homeassistant-custom-component`
- **HACS Action**: HACS integration validation
- **Hassfest**: Home Assistant manifest validation

Run formatting and checks locally:

```bash
# Format code
ruff format custom_components/

# Run linting
ruff check custom_components/

# Type checking
ty check custom_components/

# Run tests
pytest

# Validate JSON
python -m json.tool custom_components/veeam_365/manifest.json
```

### CI/CD

All pull requests are automatically validated with:
- Python code formatting and linting (Ruff)
- Type checking (ty)
- Tests (pytest)
- HACS validation
- Home Assistant manifest validation (hassfest)
- JSON validation

## License

This project is licensed under the terms included in the LICENSE file.

## Credits

This integration uses the [veeam-365](https://github.com/Cenvora/veeam-365) Python library for communication with Veeam Backup for Microsoft 365 servers. The library is automatically installed by Home Assistant when you add this integration - no manual installation required.
