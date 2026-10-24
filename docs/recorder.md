# Recorder and database growth

The integration does not create a database or insert rows itself. Home
Assistant Recorder stores entity changes. The integration controls how often
it reads measurements and rounds native values before publishing them.

Version 0.2.1 uses a **30-second measurement interval** by default,
including for existing entries that do not yet have that option. Status,
errors and control readback keep their **five-second** default. A charging or
connection state change refreshes measurements and lifetime energy in the same
status poll. Without a vehicle, measurement reads are at least 60 seconds
apart. Longer configured intervals remain effective. The heartbeat is separate.

A longer interval reduces the resolution of power graphs and any automation
using these measurements. Choose five seconds in the integration options if
that resolution is necessary, and exclude detail sensors from Recorder if
you do not need their history. The options do not alter Recorder globally.

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

## Keep history bounded

Merge this into your existing `recorder:` section in `configuration.yaml`;
there may only be one such section. Keep your existing filters and database
connection settings. This example keeps ten days of detailed history and
omits per-phase measurements and session duration:

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

The integration cannot guarantee that the whole Home Assistant database will
never fill the disk. Keep automatic purging enabled, retain headroom for
repacking, and monitor actual database size and host free space. This audit
has not changed any live Recorder configuration or deleted existing history.
