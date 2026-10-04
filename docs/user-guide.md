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
reads the register blocks the device supports — eighteen contiguous ranges
rather than fifty single registers. A block the device rejects is retried
register by register, and a block that keeps failing is reported in
diagnostics without taking the rest of the snapshot with it.

The heartbeat is **not** part of the poll. It runs in its own background task
on a fixed five-second interval, because a slow or failing poll must never be
able to starve it — that is the documented way into error state 200.

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

**The config flow says it cannot connect.** Check the wiring (Modbus `A` is
`+`, `B` is `−`), that the adapter path is right, and the bus parameters.
The shipped bus check isolates this from Home Assistant; see the
[quickstart](quickstart.md) for the command on your installation type.

**The config flow says the device gave no layout version.** Something answered
on the bus but not with a layout version. Check the Modbus device address and
whether DIP 4 and DIP 5 on bank S1 are on — the wallbox needs a restart after
changing them.

**Writes are refused.** Same cause: a wallbox that is not in satellite mode
answers reads but refuses writes. The integration raises a repair issue for
this.

**Error 200 / "Energy manager unavailable".** The wallbox did not get a
heartbeat in time. Use the repair issue's *Fix* button, or press
`button.<name>_recover_from_error`; both write the documented recovery
sequence. If it keeps happening, the serial link is dropping frames — check
the cabling, the termination and the ground connection.

**Some entities are missing.** The device's register layout is older than
v01.03 and does not have those registers, or the hardware cannot switch
phases. `sensor.<name>_modbus_layout_version` (disabled by default) shows the
layout; a repair issue appears for anything older than v01.03.

**The charging current does not follow immediately.** The manufacturer allows
one change every five seconds. A faster change is remembered and written when
the interval has passed, so the last value you set always wins.

**The slider jumps back.** While charging is paused the register holds the
pause value, which is below the entity's minimum. The entity then shows the
stored setpoint. Turn off `switch.<name>_charging_paused` to resume.

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
