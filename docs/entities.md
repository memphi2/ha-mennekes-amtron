# Entity Reference

Every entity this integration can create, what it means, and when it exists.

Entity ids are shown without the device prefix: Home Assistant builds them
as `<platform>.<device name>_<key>`, and the device is called
`MENNEKES AMTRON` unless you rename it.

The **Needs** column says what the wallbox has to support. A register that
a firmware does not have produces no entity at all, rather than an entity
stuck at `unknown` -- see [quality-scale.md](quality-scale.md) for why.

Control entities exist only while the entry's control mode is **Modbus
master**. A read-only entry has sensors and binary sensors and nothing else.

This file is generated from the entity descriptions;
`scripts/check_register_map.py` fails the build if an entity is missing
from it.

## Sensors

| Entity | Name | Unit | Class | Needs | Meaning |
|---|---|---|---|---|---|
| `sensor.*_evse_state` | EVSE state |  | enum | always | What the wallbox itself thinks it is doing. The main state sensor. |
| `sensor.*_cp_state` | CP state |  | enum | layout v01.02 | The control-pilot signal between wallbox and vehicle. `c2` means the vehicle is actually drawing current. |
| `sensor.*_authorization_status` | Authorization |  | enum | always | Whether RFID or the energy manager has released this session. |
| `sensor.*_error_code` | Error code |  | enum | always | The active error, as a documented category. The raw number is in the `code` attribute. |
| `sensor.*_detected_ev_phases` | Detected vehicle phases |  | enum | layout v01.02 | How many phases the vehicle used in this session. |
| `sensor.*_signaled_current` | Signaled current | A | current | layout v01.03 | What the wallbox offers the vehicle on the pilot line. This is the number that should follow your charging-current limit. |
| `sensor.*_current_l1` | Current L1 | A | current | always | RMS output current on L1. |
| `sensor.*_current_l2` | Current L2 | A | current | always | RMS output current on L2. |
| `sensor.*_current_l3` | Current L3 | A | current | always | RMS output current on L3. |
| `sensor.*_voltage_l1` | Voltage L1 | V | voltage | always | RMS output voltage on L1. |
| `sensor.*_voltage_l2` | Voltage L2 | V | voltage | always | RMS output voltage on L2. |
| `sensor.*_voltage_l3` | Voltage L3 | V | voltage | always | RMS output voltage on L3. |
| `sensor.*_power_l1` | Power L1 | W | power | always | Active power on L1. |
| `sensor.*_power_l2` | Power L2 | W | power | always | Active power on L2. |
| `sensor.*_power_l3` | Power L3 | W | power | always | Active power on L3. |
| `sensor.*_power_total` | Total power | W | power | always | Active power on all phases. |
| `sensor.*_session_max_current` | Session maximum current | A | current | always | The highest current this session may use, after every static limit the wallbox applies. |
| `sensor.*_session_energy` | Session energy | kWh | energy | always | Energy transferred in the current session. |
| `sensor.*_session_duration` | Session duration | s | duration | always | Length of the current session. |
| `sensor.*_energy_total` | Total energy | kWh | energy | layout v01.02 | Lifetime energy on the AC port. Usable as an Energy dashboard source; the manufacturer marks it as **not usable for billing**. |
| `sensor.*_sessions_total` | Charging sessions |  |  | layout v01.02 | Lifetime number of charging sessions. |

## Diagnostic sensors

These sit under *Diagnostic* on the device page.

| Entity | Name | Unit | Needs | Meaning |
|---|---|---|---|---|
| `sensor.*_temperature` | Temperature | °C | layout v01.02 | Temperature inside the wallbox. |
| `sensor.*_modbus_layout_version` | Modbus layout version |  | always | The register layout the firmware implements. Decides which entities exist. Disabled by default. |
| `sensor.*_downgrade_current` | Downgrade current | A | always | The current limit the wallbox applies while a grid-operator downgrade is active. |
| `sensor.*_max_current_house` | Maximum house current | A | always | The installation limit set with DIP switches 6-8 on bank S2. |
| `sensor.*_max_evse_current` | Maximum wallbox current | A | always | The wallbox's own maximum, set during installation. The upper bound of the charging-current limit. |
| `sensor.*_ems_fallback_behaviour` | Energy manager fallback |  | layout v01.02 | What the wallbox does when the heartbeat stops: keep the last values, pause, or fall back to a fixed current. |
| `sensor.*_ems_fallback_current` | Energy manager fallback current | A | layout v01.02 | The fallback current, and only that: empty when the register holds a mode instead. |
| `sensor.*_grid_imbalance_threshold` | Grid imbalance threshold | A | layout v01.02 | The imbalance threshold, when imbalance monitoring is on. |
| `sensor.*_solar_min_current` | Sunshine+ minimum current | A | layout v01.02 | Minimum current in Sunshine+ mode. |
| `sensor.*_phase_switching_pause` | Phase switching pause | s | layout v01.02 | How long the wallbox pauses when it switches between one and three phases. |
| `sensor.*_phase_rotation` | Phase rotation |  | always | The order of the connected phases, which matters for load management. |
| `sensor.*_switched_phases` | Switched phases |  | layout v01.02 | Which phases the wallbox will use when it next closes the relay. |
| `sensor.*_cable_lock_status` | Cable lock |  | always | Cable locking. A 4You 310 has a fixed cable and reports `fixed_cable`. |
| `sensor.*_phase_switching_mode` | Phase switching mode |  | always | How the wallbox's internal solar algorithm may use phases. |
| `sensor.*_grid_phases_connected` | Connected grid phases |  | layout v01.02 | How many grid phases are wired to the wallbox. |

## Binary sensors

| Entity | Name | Class | Needs | Meaning |
|---|---|---|---|---|
| `binary_sensor.*_vehicle_connected` | Vehicle connected | plug | always | A vehicle is plugged in, in any state from connected to charging. |
| `binary_sensor.*_charging` | Charging | battery_charging | always | The wallbox reports the charging state. |
| `binary_sensor.*_downgrade_active` | Downgrade active |  | always | The grid operator's downgrade input is active and the wallbox is limiting the current. **This is the §14a EnWG visibility.** |
| `binary_sensor.*_error` | Error | problem | always | An error code other than zero is active. |
| `binary_sensor.*_master_lost_fallback` | Energy manager fallback active | problem | layout v01.02 | The wallbox has fallen back because the energy manager stopped answering. |
| `binary_sensor.*_authorization_enabled` | Authorization enabled |  | layout v01.02 | RFID authorization is switched on in the wallbox. |
| `binary_sensor.*_grid_imbalance_enabled` | Grid imbalance monitoring |  | layout v01.02 | Imbalance monitoring is switched on. |
| `binary_sensor.*_cable_lock_enabled` | Permanent cable lock |  | layout v01.02 | Permanent cable locking is switched on. |
| `binary_sensor.*_phase_switch_capable` | Phase switching supported |  | layout v01.01 | The hardware can switch between one and three phases. |

## Controls

Only in **Modbus master** mode.

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `number.*_charging_current_limit` | Charging current limit | always | The charging-current limit sent to the wallbox. Starts at 6 A and never goes lower; pausing and "no limit" have their own controls. |
| `switch.*_charging_paused` | Charging paused | always | Pauses by signalling 0 A to the vehicle, the manufacturer's documented way. Does not switch the relay. |
| `switch.*_charging_release` | Charging release | always | Opens and closes the wallbox relay. Use the pause switch for everyday pausing; switching the relay wears it out. |
| `switch.*_lock_evse` | Wallbox locked | always | Locks the wallbox so it refuses to charge at all. |
| `select.*_solar_charging_mode` | Solar charging mode | always | The solar mode shown on the wallbox display. Drives the HMI only, and needs DIP 7 on. |
| `select.*_requested_phases` | Requested phases | hardware that can switch phases | Single-phase or all-phase charging. Only exists on hardware that can switch. |
| `button.*_recover_from_error` | Recover from error | always | Writes the documented recovery sequence after a lost heartbeat. |
| `button.*_restart` | Restart | layout v01.03 | Restarts the wallbox. Refused unless the wallbox is idle. |

## Enumerated states

What the enum entities can report. The option names are what automations
and templates see.

| Entity | States |
|---|---|
| `sensor.*_evse_state` | `charging`, `error`, `ev_connected`, `idle`, `not_initialized`, `preconditions_valid`, `ready_to_charge`, `service_mode` |
| `sensor.*_cp_state` | `a1`, `a2`, `b1`, `b2`, `c1`, `c2`, `d1`, `d2`, `e`, `f`, `init` |
| `sensor.*_authorization_status` | `authorized`, `not_authorized`, `not_used` |
| `sensor.*_error_code` | `connected_phases_mismatch`, `energy_manager_unavailable`, `ev_overcurrent`, `no_error`, `other`, `voltage_out_of_range` |
| `sensor.*_detected_ev_phases` | `not_initialized`, `one_phase`, `three_phases`, `two_phases` |
| `sensor.*_ems_fallback_behaviour` | `disabled`, `fallback_current`, `pause_on_timeout` |
| `sensor.*_phase_rotation` | `l1_l2_l3`, `l2_l3_l1`, `l3_l1_l2` |
| `sensor.*_switched_phases` | `all_available`, `single_phase` |
| `sensor.*_cable_lock_status` | `fixed_cable`, `locked`, `undetermined`, `unlocked` |
| `sensor.*_phase_switching_mode` | `solar_dynamic`, `solar_one_phase`, `solar_three_phases` |
| `sensor.*_grid_phases_connected` | `l1`, `l1_l2_l3` |
| `select.*_solar_charging_mode` | `fast`, `not_active`, `sunshine`, `sunshine_plus` |
| `select.*_requested_phases` | `all_available`, `single_phase` |

## The action

`mennekes_amtron.set_charging_current` writes the charging-current limit
and is the only way to reach the "no limitation" value. See
[user-guide.md](user-guide.md#actions).
