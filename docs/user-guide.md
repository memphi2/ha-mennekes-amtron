# User Guide

## Supported devices

| Device | Status |
|---|---|
| AMTRON 4You 310 11 C2 (article `1313201205`) | Validated |
| AMTRON 4You 300 | Expected to work, untested |
| AMTRON Compact 2.0s | Expected to work, untested |
| AMTRON Start 2.0s | Expected to work, untested |

All four are covered by the same MENNEKES Modbus RTU specification. The
integration reads the register layout version from the device and only creates
entities for registers that layout actually has, so an older firmware yields
fewer entities instead of broken ones.

Requirements: the wallbox has to be in Modbus satellite mode (DIP bank S1,
DIP 4 and DIP 5 on, then restart) and reachable over RS-485 from the Home
Assistant host.

## Supported functions

**Reading**

- Charging state (EVSE state, CP state, authorization, error code)
- Live measurements: current, voltage and power per phase, total power
- Signaled current, i.e. what the wallbox actually offers the vehicle
- Charging session: energy, duration, maximum current, detected vehicle phases
- Lifetime totals: energy and number of sessions
- Configuration readback: current limits, phase options, cable lock, grid
  imbalance, Sunshine+ minimum current, phase-switching pause
- Downgrade state and downgrade current (§14a visibility)
- Internal temperature

**Writing** (only in *Modbus master* control mode)

- Charging-current limit
- Pause and resume charging
- Charging release (the wallbox relay)
- Lock and unlock the wallbox
- Solar charging mode shown on the wallbox display
- Requested phases, on hardware that supports switching
- Recovery sequence after a lost heartbeat
- Restart, while the wallbox is idle

## Configuration parameters

Set when the entry is created, changeable with *Reconfigure*:

| Parameter | Default | Notes |
|---|---|---|
| Serial port | — | Prefer `/dev/serial/by-id/...`; `/dev/ttyUSB0` moves |
| Modbus device address | `50` | `10`–`50`, changeable with the MENNEKES tool |
| Baud rate | `57600` | Factory default |
| Data bits | `8` | Factory default |
| Parity | `N` | Factory default |
| Stop bits | `2` | Factory default is 2 stop bits with no parity |

Changeable any time in the entry options:

| Option | Default | Notes |
|---|---|---|
| Control mode | `Read only` | `Modbus master` enables the heartbeat and the control entities |
| Polling interval | `5 s` | `2`–`300 s`. The heartbeat is independent of this |
| Charging current cap | device maximum | An extra upper bound for the number entity |

Changing an option reloads the entry.

## How data is updated

The wallbox has no push channel, so the integration polls. Every interval it
reads the register blocks the device supports — contiguous ranges rather than
fifty single registers. A block the device rejects is retried register by
register, and a block that keeps failing is reported in diagnostics without
taking the rest of the snapshot with it.

Not every block is read every time. Half of the register map is
configuration — the serial number, the article number, the DIP-configured
limits, the hardware phase option, the lifetime counters — and those change
when somebody reconfigures the wallbox, not while it charges. They are read
once a minute and carried forward in between, which leaves the bus to the ten
blocks that actually move: state, control pilot, signalled current, the
current limits, the measurements, the session, the functions and the error
registers. A reconfiguration still shows up on its own, within a minute.

The measurements follow the vehicle rather than the clock. While nothing is
plugged in, the currents and powers are zero and only the mains voltage keeps
moving, so those blocks drop to the once-a-minute cadence too. They go back to
your interval the moment the wallbox reports a connected vehicle. The sensors
keep their values and stay available throughout.

The heartbeat is **not** part of the poll. It runs in its own background task
on a fixed five-second interval, because a slow or failing poll must never be
able to starve it — that is the documented way into error state 200.

## Database load

Home Assistant writes a row every time an entity's state changes, so a short
polling interval is also a decision about your recorder database. On a
five-second interval this integration writes roughly **35 000 rows a day**,
most of them during the hours a car is actually charging. With the default
ten-day purge that is around 350 000 rows.

Two things already keep that down: numeric states are rounded to the precision
they are displayed at, instead of storing the `230.10000610351562` that a
float32 decodes into, and the measurement blocks stop being polled while the
wallbox is idle. Without those the same wallbox writes about 78 000 rows a day.

If you want less, in rising order of effect:

- **Raise the polling interval.** The cost is linear: 10 s halves the rows,
  30 s divides them by six. Controls stay responsive either way, because the
  integration refreshes immediately after every write rather than waiting for
  the next poll.
- **Stop recording the per-phase measurements.** They are the bulk of it, and
  the totals are usually what gets used:

  ```yaml
  recorder:
    exclude:
      entities:
        - sensor.mennekes_amtron_voltage_l1
        - sensor.mennekes_amtron_voltage_l2
        - sensor.mennekes_amtron_voltage_l3
        - sensor.mennekes_amtron_power_l1
        - sensor.mennekes_amtron_power_l2
        - sensor.mennekes_amtron_power_l3
  ```

  Keep `power_total` and `energy_total` — the Energy dashboard needs them.
- **Disable the entities you do not use.** A disabled entity is not recorded
  at all. The diagnostic sensors are the obvious candidates.

## Use cases

**Solar surplus charging.** Feed your PV surplus into
`number.<name>_charging_current_limit`. Compute amps as
`surplus_watts / 230 / phases`, clamp to at least 6 A, and let the automation
pause through `switch.<name>_charging_paused` when the surplus disappears.
Keep at least five minutes between pause and resume.

**Dynamic load management.** Subtract the rest of the house from your main
fuse rating and write the remainder as the charging-current limit. The wallbox
clamps anything you send to its own configured maximum, so an over-eager
automation cannot exceed the installation limit.

**Cheap-tariff charging.** Use `switch.<name>_charging_release` or a schedule
on the current limit to charge during a cheap window.

**§14a transparency.** Watch `binary_sensor.<name>_downgrade_active` and
`sensor.<name>_downgrade_current` to see when the grid operator dims your
wallbox and by how much.

**Energy dashboard.** Add `sensor.<name>_energy_total` as a consumption
source. The manufacturer marks this register as not usable for billing.

## Examples

Charge with PV surplus, in 1 A steps, never below 6 A:

```yaml
automation:
  - alias: Wallbox follows PV surplus
    triggers:
      - trigger: state
        entity_id: sensor.pv_surplus_power
    conditions:
      - condition: state
        entity_id: binary_sensor.amtron_vehicle_connected
        state: "on"
    actions:
      - action: number.set_value
        target:
          entity_id: number.amtron_charging_current_limit
        data:
          value: >-
            {{ [[ (states('sensor.pv_surplus_power') | float(0)
                   / 230 / 3) | round(1), 6 ] | max, 16 ] | min }}
    mode: single
```

Pause when the surplus is gone, with the manufacturer's hysteresis:

```yaml
automation:
  - alias: Wallbox pauses without surplus
    triggers:
      - trigger: numeric_state
        entity_id: sensor.pv_surplus_power
        below: 1380
        for: "00:05:00"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.amtron_charging_paused
```

Remove the limit entirely, deliberately:

```yaml
actions:
  - action: mennekes_amtron.set_charging_current
    target:
      device_id: !input wallbox
    data:
      current: 0
      allow_unlimited: true
```

## Actions

### `mennekes_amtron.set_charging_current`

Writes the energy-manager charging-current limit (`0x0302`) of one or more
wallbox devices.

| Field | Required | Meaning |
|---|---|---|
| `current` | yes | Limit in ampere, `0` or `6`–`32` |
| `allow_unlimited` | no, default `false` | Required to write `0 A` |

`0 A` does **not** stop charging: it removes the limit and the wallbox signals
its maximum current. Values between `0.01 A` and `5.99 A` are rejected,
because the wallbox treats them as invalid and signals 0 A — use
`switch.<name>_charging_paused` to pause instead.

The action exists because this one argument cannot be offered safely as an
entity. Everything else is an entity.

## Troubleshooting

See [troubleshooting.md](troubleshooting.md). The short version: the setup
form can search the bus for you, the diagnostics download carries the full raw
register image, and four repair issues explain the four ways this device gets
stuck.

## Known limitations

- Only one wallbox per config entry, and one master per RS-485 bus.
- Validated only on the AMTRON 4You 310 11 C2 with layout v01.03.
- No discovery: a USB RS-485 adapter cannot be identified as this wallbox, so
  the port is chosen manually.
- `sensor.<name>_energy_total` is explicitly not usable for billing.
- The error-code sensor reports the categories the specification documents;
  anything else becomes `Other error` with the raw code as an attribute.
- Writing the solar charging mode only drives the wallbox display and needs
  DIP 7 on; the energy manager still owns the solar algorithm.
- The integration cannot bypass a §14a downgrade, a DIP-configured
  installation limit or the EVSE maximum, and is not intended to.
