# Recorder and database growth

The integration reduces routine updates but **does not impose a database size
limit**. Home Assistant Recorder stores entity changes. Keep automatic purging
enabled and monitor database size and free disk space. Long-term statistics
are not removed by ordinary history retention.

## Choose the history you need

Keep the default **30-second measurement interval** unless you need faster
power/current readings. Status and controls keep a separate **five-second**
default, and the heartbeat is independent. Unchanged values do not produce
extra state rows. A longer interval reduces detail in graphs and automations;
it does not reduce the number of hourly statistics series.

| Profile | What to configure below | Statistics series | Hourly rows per day |
|---|---|---:|---:|
| Full history | Keep all default sensors recorded | 17 | 408 |
| Less detail | Exclude phase current/voltage/power and session duration | 7 | 168 |
| Energy and power statistics only | Also exclude the five extra sensors listed below | 2 | 48 |

Counts assume one wallbox on layout v01.03, default enabled sensors and valid
values throughout the day. They are statistics-row counts, **not disk-space
limits**. State history for other entities can still be recorded in the smaller
profiles. All excluded sensors remain usable live; exclusion only stops their
new history and statistics. Existing statistics are retained.

## Configure history retention and detail exclusions

Back up your configuration, then merge this into your existing `recorder:`
section in `configuration.yaml`; there may only be one such section. Keep your existing filters and database
connection settings. Retention and commit settings affect **all** recorded
entities, not only this integration. This example keeps ten days of detailed
history and omits per-phase measurements and session duration:

```yaml
recorder:
  auto_purge: true
  auto_repack: true
  purge_keep_days: 10
  commit_interval: 30
  exclude:
    entities:
      - sensor.mennekes_amtron_current_l1
      - sensor.mennekes_amtron_current_l2
      - sensor.mennekes_amtron_current_l3
      - sensor.mennekes_amtron_voltage_l1
      - sensor.mennekes_amtron_voltage_l2
      - sensor.mennekes_amtron_voltage_l3
      - sensor.mennekes_amtron_power_l1
      - sensor.mennekes_amtron_power_l2
      - sensor.mennekes_amtron_power_l3
      - sensor.mennekes_amtron_session_duration
```

Check Home Assistant configuration and restart to apply changes to Recorder.

These IDs assume the default device name and English entity names. Check the
actual IDs under **Settings → Devices & services → Entities**, especially
with renamed devices, another language, or multiple wallboxes. The integration
domain `mennekes_amtron_rtu` does not add `rtu` to these entity IDs.

Keep **`sensor.mennekes_amtron_total_energy`** recorded for the Energy
dashboard, along with **`sensor.mennekes_amtron_total_power`** if you want
power history. Excluded entities remain usable live, but they no longer get
new history or statistics. Disabling an entity also removes its live state.

`commit_interval: 30` batches database commits; it does **not** reduce the
number of recorded state changes. Purging removes old history, and repacking
can reclaim space. These settings and the need for temporary free disk space
are documented by [Home Assistant Recorder](https://www.home-assistant.io/integrations/recorder/).

Hourly long-term statistics are **not deleted automatically** by
`purge_keep_days`. They continue to grow even with a short history retention
period; excluding unused detail sensors prevents new statistics for those
sensors. Existing statistics remain until explicitly deleted. See
[Home Assistant long-term statistics](https://www.home-assistant.io/docs/configuration/long-term-statistics/).

## Keep only energy and power statistics

For the smallest profile, merge this **complete list** into your existing
`recorder.exclude.entities` list; it includes the detail exclusions above.
Replace the example IDs with the actual IDs of your wallbox. Keep your
existing Recorder settings and any other exclusions.

```yaml
recorder:
  exclude:
    entities:
      - sensor.mennekes_amtron_current_l1
      - sensor.mennekes_amtron_current_l2
      - sensor.mennekes_amtron_current_l3
      - sensor.mennekes_amtron_voltage_l1
      - sensor.mennekes_amtron_voltage_l2
      - sensor.mennekes_amtron_voltage_l3
      - sensor.mennekes_amtron_power_l1
      - sensor.mennekes_amtron_power_l2
      - sensor.mennekes_amtron_power_l3
      - sensor.mennekes_amtron_session_duration
      - sensor.mennekes_amtron_signaled_current
      - sensor.mennekes_amtron_downgrade_current
      - sensor.mennekes_amtron_session_energy
      - sensor.mennekes_amtron_charging_sessions
      - sensor.mennekes_amtron_temperature
```

Keep total energy and total power recorded. Check the configuration and
restart Home Assistant. Confirm their history continues and the excluded
sensors still show live readings. Existing statistics are not deleted.

This leaves two statistics series (48 hourly rows per day), with live states
still available for all excluded sensors. It also removes their new detailed
history. Existing statistics are retained. The integration never changes
Recorder filters or deletes historical data on its own.

## Measured load

Audit: 2026-10-24, Home Assistant 2026.10.0, SQLite, one integration entry.
The released v0.2.0 cadence was compared with the 30-second measurement
default in v0.2.1. Each scenario replays one synthetic day through the real coordinator,
entities, event bus and Recorder; only the Modbus transport is simulated.

| Synthetic day | v0.2.0, 5-second polling | 30-second measurements | Reduction |
|---|---:|---:|---:|
| Unchanged register values | — | 0 | — |
| Unplugged, voltage fluctuating | 4,933 | 4,933 | 0% |
| Plugged in, not charging | 35,634 | 8,986 | 75% |
| Unplugged 20 h, charging 4 h | 35,036 | 10,120 | 71% |
| Charging continuously | 185,527 | 36,031 | 81% |

Counts are additional **state rows**, after initial entity setup. They are
scenario results, not upper bounds. Voltage varies by ±0.8 V around 230 V;
charging current varies around 15 A per phase. Different meters, errors,
status transitions, user automations and interval settings change the result.
Unchanged attributes used the same 44 attribute records in every scenario.
There are no per-poll timestamps or growing register dumps on the entities.

For continuous charging, SQLite allocated about 29.2 MiB before and 5.6 MiB
after the change; the mixed day allocated 5.4 MiB and 1.7 MiB. These sizes
include SQLite schema and indexes. They exclude a real day's statistics,
purge/repack behavior, other integrations, backups and temporary WAL storage.
Do not multiply them into a guaranteed disk-space requirement.

Reproduce the current behavior in a **new** output directory:

```bash
.venv/bin/python -m tests.recorder_replay /tmp/amtron-recorder-30 --measurement-interval 30
.venv/bin/python -m tests.recorder_replay /tmp/amtron-recorder-5 --measurement-interval 5
```

The harness writes JSON counts and disposable SQLite databases outside the
repository. It neither contacts a wallbox nor modifies a running Home
Assistant. Time advances only for the integration polling logic: this is
not a 24-hour soak test of Recorder statistics and retention. The automated
suite also checks unchanged values, reduced row counts and retained total
power/energy recording with shorter replays.

### Full history versus the lean profile

Repeated on 2026-11-01 with HA 2026.10.0, a 30-second measurement interval
and the same synthetic continuous-charging day:

| Profile | Additional state rows | Attribute rows | SQLite allocation |
|---|---:|---:|---:|
| Full | 36,031 | 44 | 5,849,088 bytes |
| Lean, complete list above | 4,320 | 29 | 843,776 bytes |

The lean profile produced **88% fewer state rows** in this scenario. Both
recorded the same 2,877 total-power changes and 1,439 total-energy changes;
the other four retained changes describe charging/connection state. These
are replay measurements with the limitations above, not storage guarantees.

```bash
.venv/bin/python -m tests.recorder_replay /tmp/amtron-full --scenario charging --profile full
.venv/bin/python -m tests.recorder_replay /tmp/amtron-lean --scenario charging --profile lean
```

## November compatibility and verification

Home Assistant 2026.11 requires `mean_type` and `unit_class` for integrations
that import or modify statistics through the Recorder API. This integration
uses ordinary sensor states instead: Home Assistant derives the metadata
from the state class, device class and unit. The lifetime energy counter is
`total_increasing` in kWh, and total power is `measurement` in W. No custom
statistics import or migration is required. See the
[Recorder API announcement](https://contributors.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/).

`tests/test_statistics.py` compiles a synthetic hour through the real Recorder
and SQLite. It checks energy increments, power averages, metadata and all
three exclusion profiles, including that excluded sensors remain available
live. This complements the state-row replay; it is not a disk-space soak test.

The November beta also adds deletion of selected statistic time ranges in
**Settings → Tools → Statistics**, useful for removing bad readings. Inspect
the preview before deleting; it is not automatic retention. See the
[2026.11 beta notes](https://rc.home-assistant.io/blog/2026/10/28/release-202611/).

The integration cannot guarantee that the whole Home Assistant database will
never fill the disk. Keep automatic purging enabled, retain headroom for
repacking, and monitor actual database size and host free space. This audit
has not changed any live Recorder configuration or deleted existing history.
