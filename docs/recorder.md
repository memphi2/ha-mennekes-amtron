# Recorder and database growth

For a smaller history, keep the default measurement interval and use the
**Energy and power statistics only** profile below. All readings remain available
live, and total energy remains available for the Energy dashboard.

The integration does not set a database size limit. Keep automatic purging
enabled and monitor free disk space; hourly long-term statistics continue growing
even with short history retention.

## Recorder defaults

Every sensor starts enabled. Readings that only detail another sensor are its
attributes rather than entities of their own: the phase voltages under load
ride on **Total power** (whole volts, and only while power flows, so a
wobbling mains voltage writes nothing while the wallbox is idle), the
session duration, maximum current and session count on **Session energy**
(the duration in minutes, so it changes once a minute), the installation
settings on **Wallbox settings**. Attributes are stored with the state they
belong to and never compile long-term statistics of their own. Statistics
series existing from earlier versions are retained.

The **Control result** event entity starts disabled because frequent PV current
commands can create many outcomes. Enable it only if you need those events for
automations. Its feed is bounded in memory, but recording an enabled entity adds
one state change per observed outcome: a command every five seconds could add
17,280 rows per day. Exclude its actual entity ID from `recorder.exclude.entities`
if you only need live notifications. Exclusion does not stop automation triggers.
Heartbeats and polls create no command events.

The **Wallbox event** entity is enabled by default. It records one state
change per reported wallbox change, such as a plug-in, a charge start or a
fault: a handful of rows per charging session, none while nothing happens.

The new charging/quality/goal status entities use stable enum states and have
no long-term statistics. The energy target changes only when you set it. Its
baseline and stop state are saved outside Recorder, only on changes; ordinary
meter progress creates no extra storage writes. The existing total-energy sensor
keeps its normal measurement/history behavior.

The optional vehicle percentage target listens to existing HA entity updates;
it adds no vehicle polling and no duplicate SoC history sensor. Only the chosen
target, optional maximum and reached state are saved, not every vehicle report.
Changing a dynamic vehicle maximum can update the target number's maximum
attribute; stable values produce no additional Recorder changes.

## Choose the history you need

Keep **30-second measurements** unless your automations need faster readings.
Status and control readback default to **five seconds**; the heartbeat runs
independently. Unchanged values create no extra state rows. Longer measurement
intervals reduce graph detail, but do not reduce the number of hourly statistics
series.

| Connection | Where to set the intervals |
|---|---|
| Direct USB or LAN | Integration entry options: **Status polling interval** and **Measurement interval**. |
| Service | App or standalone service configuration: status and measurement intervals. HA entry options only control how often HA fetches the service's cached status. |

Heartbeat health is the `heartbeat` attribute of **Connection quality**. It
changes only between `ok` and `problem`, without a per-heartbeat timestamp or
counter.

| Profile | History retained |
|---|---|
| **Full history** | All default enabled sensors. |
| **Less detail** | Excludes signaled current and temperature. |
| **Energy and power statistics only** | Retains statistics for total energy and total power; other retained states still have history. |

Exclusion stops new history and statistics for the selected sensors. They stay
available live; their existing history and statistics are not deleted. Disabling
an entity instead also removes its live state.

## Configure history retention and detail exclusions

Generate the correct entity IDs instead of copying an example:

1. Open **Developer tools → Actions → Mennekes Amtron RTU: Suggest Recorder
   exclusions** (`mennekes_amtron_rtu.get_recorder_exclusions`).
2. Select your wallbox or wallboxes and choose a profile.
3. Run the action and merge the returned `entities` list into your existing
   `recorder.exclude.entities` in `configuration.yaml`. Keep other exclusions
   and database settings; there may only be one `recorder:` section.
4. Check the Home Assistant configuration and restart to apply the changes.

The action only generates a suggestion. It changes no configuration, deletes no
history and sends no wallbox commands. It also works in read-only mode. Renamed
entities and multiple wallboxes are handled automatically; regenerate the list
after renaming entities or adding wallboxes. **Full history** returns an empty
list and does not undo existing exclusions.

For retention, merge these example settings into the same `recorder:` section
alongside your exclusions. Back up the configuration first. They affect **all**
recorded entities, not only the wallbox:

```yaml
recorder:
  auto_purge: true
  auto_repack: true
  purge_keep_days: 10
  commit_interval: 30
```

This keeps ten days of detailed history. `commit_interval: 30` batches commits;
it does not reduce recorded state changes. Purging removes old history, and
repacking can reclaim space. See [Home Assistant Recorder](https://www.home-assistant.io/integrations/recorder/).

`purge_keep_days` does not delete hourly long-term statistics. Excluding unused
sensors stops their new statistics, while existing statistics remain. See
[Home Assistant long-term statistics](https://www.home-assistant.io/docs/configuration/long-term-statistics/).

## Keep only energy and power statistics

Choose **Energy and power statistics only** in the action above and apply its
complete exclusion list. Keep **Total energy** recorded for the Energy dashboard,
and **Total power** for power history. Their default English IDs are
`sensor.mennekes_amtron_total_energy` and `sensor.mennekes_amtron_total_power`;
check the actual IDs if your device is renamed or uses another language.

After restarting, confirm that energy and power history continues and the
excluded sensors still show live readings. The profile leaves two statistics
series per wallbox; it does not limit the size of the whole database or remove
existing statistics.

## Monitor free space on the database disk

Add [System Monitor](https://www.home-assistant.io/integrations/systemmonitor/)
and enable **Disk free** for the filesystem containing the Recorder database.
Its entities are disabled by default. Verify the mount, especially with
containers; monitor an external database on its own host.

Choose a threshold that leaves room for maintenance, backups and WAL growth.
Home Assistant needs at least the database's size again as free temporary space
for operations such as repacking. See [Recorder disk-space requirements](https://www.home-assistant.io/integrations/recorder/#disk-space-requirements).
Monitor actual database size as well; a fixed percentage alone may leave too
little headroom.

Create an automation that checks **Disk free** at HA startup and every 15 minutes.
Below your chosen threshold, create a persistent notification with a fixed ID so
repeated checks replace it. Check the sensor's unit and test the notification.

## Measured load

Replay results, retention checks and compatibility evidence are in
[Recorder validation and measurements](recorder-validation.md). These are
controlled test results with stated limits, not storage guarantees.
