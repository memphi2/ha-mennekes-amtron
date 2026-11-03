# Using the wallbox

Start with [Quick start](quickstart.md) if you have not added the integration.
The device page is under **Settings → Devices & services → Mennekes Amtron RTU**.
Labels below use English; the UI also provides German
translations.

## Everyday controls

Controls appear only in **Modbus master** mode. A new entry starts read-only;
change this in the entry options after checking the readings and exclusive
bus access.

| Control | How to use it |
|---|---|
| **Charging current limit** | Enter **0 A** to turn the limit off, or **6 A** up to the displayed maximum to set a limit. Changing it while paused can resume charging. |
| **Charging paused** | **On** pauses; **off** resumes using the remembered current. Use this for ordinary start/stop and tariff schedules. |
| **Charging release** | Controls the charging release/relay. Leave this out of frequent surplus adjustments; use the pause switch. |
| **Wallbox locked** | Prevents charging while locked. |
| **Solar charging mode** | Changes the mode shown by the wallbox; it does not calculate or follow PV surplus. It requires DIP 7 as described in the hardware guide. |
| **Requested phases** | Available only on capable hardware. See phase switching below. |
| **Recover from error** | Runs the documented sequence for a lost energy-manager heartbeat. It may release and resume charging. |
| **Restart** | Restarts the wallbox only when it reports idle. |

**0 A turns the limit off.** The wallbox's own limits still apply; the extra
current cap in the integration options is bypassed. Enter 6 A or more to
enable a limit again. Values between 0 and 6 A are rejected. Use
**Charging paused** to stop a charge.

Current requests closer than five seconds are deferred, with the newest
accepted request taking precedence. Pause/resume requests inside the
five-second waiting interval are rejected with the remaining wait time;
they are not queued for later. MENNEKES recommends more than five minutes
between repeated pause/resume cycles; use hysteresis in automations. The
bundled PV pause/resume blueprint defaults to six minutes.
Timing guards survive an entry reload. After a full Home Assistant restart
or the first setup, current writes and pause/resume wait five seconds;
phase changes still wait five minutes from setup. This protects the wallbox when
the previous write history is unknown. Reading and the heartbeat continue.

While paused, the current field shows the remembered setting, including 0
for no limit. Resuming restores that setting. The integration restores it
after a restart when available; otherwise, resuming uses the configured maximum.
Check the current field before resuming. Active PV or other current-control
automations can set a new limit again; disable them if you want to keep it off.

## Understand the readings

- **Vehicle connected** means a cable/vehicle is detected; it does not mean
  charging has started. Authorization, vehicle settings and release still matter.
- **Charging** and **EVSE state** describe the wallbox's state.
- **Signaled current** is what the wallbox offers the vehicle. It can be lower
  than your requested limit because of installation limits or a downgrade.
- **Total power** shows the actual combined power draw.
- **Session energy** belongs to the current session. **Total energy** is the
  lifetime counter to use in the Energy dashboard; it is **not usable for billing**.
- **Downgrade active** and **Downgrade current** show the wallbox's hardware
  grid-operator restriction. An automation cannot override it.

See [Entity reference](entities.md) for the full list and exact state values
used in templates. Copy actual entity IDs from the entity settings; language,
renaming and multiple devices can change them.

## Configuration and options

Use **Reconfigure** on an existing entry to change the connection, rather than
adding the wallbox again:

Changing bus parameters or starting a search temporarily stops the existing
connection and its heartbeat. Check the configured fallback first and expect
charging to pause or enter the fallback state. Failed or cancelled searches
restore the old entry. The full search has 861 candidates and roughly
15 minutes of response waits, including one read retry per candidate;
opening ports and checking a responding device can add time.

| Connection setting | Factory default |
|---|---|
| Serial port | Select your adapter; prefer `/dev/serial/by-id/...` |
| Modbus device address | `50` (documented range `10`–`50`) |
| Baud rate | `57600` |
| Frame | `8` data bits, parity `N`, `2` stop bits |

Use the entry **options** for behavior:

| Option | Default | Effect |
|---|---|---|
| Control mode | Read only | Modbus master enables writes, controls and heartbeat |
| Status polling interval | 5 seconds | State, errors and control readback; range 2–300 seconds |
| Measurement interval | 30 seconds | Current, voltage, power and session readings; range 2–300 seconds |
| Charging current cap | Device maximum | Additional upper bound for normal current commands |

Changes reload the entry. Measurements are read on status polls, so a long
status interval also limits measurement updates. With no vehicle, measurement
reads are at least a minute apart. A charging/connection transition refreshes
measurements and lifetime counters in the same status poll. Failed reads make
the affected entities unavailable; unchanged values remain available between
successful reads.

The heartbeat runs separately every five seconds in master mode. A stalled
serial bus or stopped Home Assistant can still interrupt it: configure and
understand the wallbox's [fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it)
before relying on unattended charging.

## Energy dashboard and database size

Select **Total energy** as the wallbox's consumption source under the Energy
dashboard's individual devices. Its default English ID is
`sensor.mennekes_amtron_total_energy`.

The 30-second measurement default reduces history traffic. It does not cap
database size, change Recorder settings or stop hourly statistics growth.
Keep total energy recorded for the Energy dashboard. Use the
[Recorder guide](recorder.md) to choose detailed history or a smaller profile
that retains only energy and power statistics while all sensors stay live.

## PV charging and schedules

Use the [PV blueprints](blueprints.md) for current adjustment and pause/resume.
They need a correctly defined power budget and thresholds matching the phase
count. Selecting **Solar charging mode** alone does not enable this behavior.
[Automations](automations.md) includes a tariff schedule and action examples.

The wallbox's configured limits restrict its own current; they do not measure
other household consumers. A simple surplus/current template is not dynamic
protection for your whole house connection.

## Phase switching

Only supported 11 kW hardware exposes **Requested phases**. Check that your
vehicle supports switching before using it. A change while charging first
pauses the current, then requests the phases; **it does not resume automatically**.
After the waiting interval, resume explicitly with the pause switch and check
the result. Some vehicles abort the session and require further intervention.

## Actions

For a Recorder exclusion list matching your actual entity IDs, use
`mennekes_amtron_rtu.get_recorder_exclusions`. Select the wallboxes and a
history profile, then merge the returned YAML into your configuration.
See [Recorder profiles](recorder.md#configure-history-retention-and-detail-exclusions).

Most automations should use the entities. The additional action
`mennekes_amtron_rtu.set_charging_current` offers an explicit unlimited option:

| Data field | Required | Meaning |
|---|---|---|
| `device_id` | Yes | One wallbox device ID or a list of device IDs |
| `current` | Yes | `6`–`32` A for a normal limit, clamped to the configured cap; `0` removes the limit |
| `allow_unlimited` | Only for `current: 0` | Must be `true` to deliberately request no energy-manager limit |

Use the action editor to select the device. `device_id` belongs inside
**`data`**, not a `target` block. A normal action looks like this; replace the
placeholder with the selected device ID:

```yaml
action: mennekes_amtron_rtu.set_charging_current
data:
  device_id: REPLACE_WITH_YOUR_DEVICE_ID
  current: 10
```

Removing the limit also bypasses the integration's extra current cap. Hardware
limits still apply. Values between 0 and 6 A are not accepted by this action;
use the pause switch instead.

**Recover from error** follows the manufacturer's recovery sequence with a
limit of at least 6 A. After recovering from an error, set the current to 0
again if you want the limit off.

## Compatible devices

See the [device compatibility matrix](compatibility.md) for supported models
and reported hardware tests.
