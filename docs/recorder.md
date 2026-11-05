# Recorder and database growth

For a smaller history, keep the default measurement interval and use the
**Energy and power statistics only** profile below. All readings remain available
live, and total energy remains available for the Energy dashboard.

The integration does not set a database size limit. Keep automatic purging
enabled and monitor free disk space; hourly long-term statistics continue growing
even with short history retention.

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

The **Heartbeat** sensor records health changes, without a per-heartbeat
timestamp or counter.

| Profile | History retained |
|---|---|
| **Full history** | All default enabled sensors. |
| **Less detail** | Excludes phase current, voltage and power, plus session duration. |
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
