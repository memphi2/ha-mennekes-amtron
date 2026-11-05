# Using your wallbox

Open **Settings → Devices & services → Mennekes Amtron RTU** to find your
wallbox. This guide uses the English labels; German translations are also
available. New here? Follow the [Quick start](quickstart.md) first.

## Everyday controls

For most charging sessions, you need just **Charging current limit** and
**Charging paused**.

| I want to… | Use |
|---|---|
| Limit charging current | Move the **Charging current limit** slider to **6 A** or more, up to its displayed maximum. |
| Remove the current limit | Set the slider to **0 A**. The wallbox's installation limits still apply. |
| Pause charging | Turn **Charging paused** **on**. |
| Resume charging | Turn **Charging paused** **off**. The remembered current setting is restored. |
| Block the wallbox from charging | Turn **Wallbox locked** **on**. |
| Withdraw charging permission | Turn **Charging release** **off**. Use the pause switch for everyday pauses. |

**0 A means no limit, not a pause.** It also bypasses the extra current caps
in the integration and service. Values between 0 and 6 A are not accepted.
Changing the slider while paused can resume charging, so use the pause switch
when your intention is to stop.

While paused, the slider shows the remembered setting, including 0 for no limit.
It does not show the current flowing to the vehicle. Check **Charging paused**,
**Signaled current** and **Total power** to see what is happening. An automation
can change a manual setting later; disable it if you want that setting to stay.

Commands that change the current or pause state have a **five-second minimum
interval**. A new current request may wait until that interval ends; only the
latest accepted request is applied. A pause/resume request made too soon is
rejected with a remaining wait time. Try again after that time. For automations,
avoid frequent pause/resume cycles; the PV blueprint defaults to six minutes.

## Where do I enable controls?

The setting depends on how you connect the wallbox:

| Connection | Where to enable writes | What you see in HA |
|---|---|---|
| **Direct serial** — the integration uses the USB/RS-485 adapter | Integration entry **options → Control mode → Modbus master**. New entries start **Read only**. | Controls are added in master mode. |
| **Modbus service** — the app uses the adapter | In the app configuration, disable **Read-only**, save and restart the app. | Controls become available after HA receives fresh service status. There is no second HA write switch. |

In service mode, enabling **Read-only** makes the controls unavailable; their
entity IDs stay the same. Stopping the app or losing its wallbox connection also
makes controls unavailable. After changing **Read-only**, save and restart the
app; HA itself needs no restart for this switch. Sensor reads continue in
read-only mode, but the heartbeat and charging commands are disabled.

Confirm that the readings match your wallbox before enabling control. Only one
connection may use the bus: read-only mode still sends Modbus requests.

## Understand the readings

| Reading | What it tells you |
|---|---|
| **Vehicle connected** | A cable/vehicle is detected. Authorization, vehicle settings and charging release still determine whether it charges. |
| **Charging** / **EVSE state** | The charging state reported by the wallbox. |
| **Signaled current** | The current offered to the vehicle. Hardware limits or a grid-operator downgrade can reduce it. The vehicle may draw less. |
| **Total power** | The actual combined charging power. |
| **Session energy** | Energy delivered during this session. |
| **Total energy** | The lifetime counter for the Energy dashboard. It is not usable for billing. |
| **Downgrade active** / **Downgrade current** | A hardware restriction from the grid operator. An automation cannot override it. |
| **Heartbeat** | Whether heartbeat delivery is healthy. It records status changes rather than every heartbeat. |

The [entity reference](entities.md) lists all entities, supported features and
the exact state values available to templates.

## Configuration and options

| Setting | Direct serial connection | Modbus service connection |
|---|---|---|
| Write permission | Integration options: **Control mode** | App configuration: **Read-only** |
| Serial adapter, address and baud rate | Integration entry: **Reconfigure** | App configuration |
| Status and measurement polling | Integration options | App configuration |
| How often HA fetches service status | Not applicable | Integration options: **Status polling interval** |
| Additional current cap for HA commands | Integration options: **Charging current cap** | Same HA option; the service's current cap also applies |

The default status interval is **5 seconds** and the measurement interval is
**30 seconds**. The available range is 2–300 seconds. In service mode, changing
HA's refresh interval does not change how often the app reads the wallbox.

For limited-current commands, the lower applicable cap wins. A cap is an upper
bound, not a command to start charging. Setting the slider to **0 A** deliberately
removes the energy-manager limit, including these additional caps.

Use **Reconfigure** or confirm the discovered app when moving an existing wallbox
to the service. Matching its serial number keeps the existing device and entity
IDs, so dashboards and automations continue to refer to them. Do not delete and
re-add the wallbox for this change. See [service setup](modbus-service.md).

## Heartbeat and Home Assistant restarts

The **optional Modbus service is recommended** if you want charging to continue
through Home Assistant Core restarts. It keeps the heartbeat running and holds
the last charging state while HA is offline, including an active pause. HA
reconnects to that state without replaying old charging commands. HA automations
are still offline during a Core restart.

With a **direct serial connection**, a Core restart or entry reload interrupts
the heartbeat. Once communication returns, error 200 recovery runs
automatically. It preserves a deliberate pause, lock, disabled charging release
and current setting. If recovery cannot complete, follow
[heartbeat troubleshooting](troubleshooting.md#error-200-energy-manager-unavailable).

The service cannot bridge its own shutdown, a host restart or a lost serial
connection. In those cases, the wallbox follows its configured
[fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).
Check that behavior before relying on unattended charging.

## Energy dashboard and database size

Add **Total energy** as a device consumption source in the Energy dashboard.
Keep that entity recorded if you want its energy statistics.

The measurement interval reduces how often detailed readings change. It does
not limit the database size or remove long-term statistics. The
[Recorder guide](recorder.md) offers a smaller history profile while keeping
all sensors live. It also explains how to retain energy and power statistics.

## PV charging and schedules

The [PV blueprints](blueprints.md) adjust current and pause/resume charging from
your available power budget. [Automation examples](automations.md) include a
tariff schedule.

**Solar charging mode** changes the mode shown on the wallbox; it does not
measure PV surplus or run a surplus-charging algorithm. It requires the hardware
configuration described in the [hardware guide](hardware.md).

Wallbox current limits do not account for other household loads. A simple PV
automation is not dynamic protection for the whole house connection.

## Phase switching

**Requested phases** appears only on hardware that supports switching. Check
your vehicle's support before using it. Switching during charging pauses first
and **does not resume automatically**. Resume explicitly after the waiting
interval and check the result; some vehicles abort the session.

Phase changes have a five-minute minimum interval. After the controlling
process starts, the same interval applies before the first phase change.
See [phase-switching troubleshooting](troubleshooting.md#phase-switching-leaves-charging-paused).

## Actions

Use the entities for ordinary automations. The action
`mennekes_amtron_rtu.set_charging_current` also supports explicit removal of
the current limit:

| Data field | Required | Meaning |
|---|---|---|
| `device_id` | Yes | A wallbox device ID, or a list of device IDs |
| `current` | Yes | `6`–`32` A, clamped to the applicable cap; `0` removes the limit |
| `allow_unlimited` | For `current: 0` | Must be `true` to remove the energy-manager limit |

Select the device in the action editor. Put `device_id` inside **`data`**, as
shown below, and replace the placeholder with your selected device ID:

```yaml
action: mennekes_amtron_rtu.set_charging_current
data:
  device_id: REPLACE_WITH_YOUR_DEVICE_ID
  current: 10
```

To generate a Recorder exclusion list using your actual entity IDs, run
`mennekes_amtron_rtu.get_recorder_exclusions`. Choose your wallboxes and history
profile, then merge the returned YAML into your configuration. See
[Recorder profiles](recorder.md#configure-history-retention-and-detail-exclusions).
