# Entity reference

Use **Charging current limit** and **Charging paused** for everyday charging.
**Total power**, **Total energy** and **Heartbeat** show power use, accumulated
energy and connection health. The [User guide](user-guide.md) explains how to
use these controls together; this page lists every entity and its state values.

The first column shows default **English** entity-ID patterns. `*` stands
for the device prefix; it is not part of a literal entity ID. For example,
Total energy normally becomes `sensor.mennekes_amtron_total_energy`.
Home Assistant derives IDs from translated names, not internal register keys.
Language, renaming and multiple devices can change them: copy your actual IDs
from **Settings → Devices & services → Entities** before using YAML examples.
Moving an existing entry to the service keeps its device and entity IDs when
the wallbox is matched by serial number.

The **Needs** column says what the wallbox has to support. A register that
a firmware does not have produces no entity at all, so fewer entities on an older layout can be expected. Optional diagnostic
entities may also need enabling on their entity settings page. A temporarily
failed read makes an existing entity unavailable until communication recovers.

| Connection | When controls are available |
|---|---|
| **Direct USB or LAN** | Select **Control mode → Modbus master** in the integration options. Read-only entries provide readings without adding controls. |
| **Modbus service** | Disable **Read-only** in the app configuration, save and restart the app. Controls become available after fresh service status arrives; HA needs no restart for this switch. Their IDs stay registered while the service is read-only, disconnected or not ready. |

## Device-page grouping — next beta, unreleased

Daily charging controls stay in **Controls**. The display-only solar selector,
recovery and restart buttons belong to **Configuration**. Technical CP status
and detailed error codes belong to **Diagnostic**, alongside heartbeat health,
internal temperature, installation limits and fallback settings. The general
**Error** sensor stays in the main status section.

The wallbox's phase selection is shown with the normal sensors; it describes
which phases the next relay closure will use. **Requested phases** is your
command, while **Detected vehicle phases** describes the vehicle's session.
These values can legitimately differ during a pending change.

Status-dependent icons distinguish paused/unpaused, locked/unlocked and
healthy/faulted states. An inactive pause does not claim that a car is charging.
Existing entity IDs, custom names and user-selected icons are retained.

**Vehicle connected** uses EVSE and CP status together. A wallbox error is not
a disconnect: a valid connected CP signal keeps it on. Insufficient or conflicting
evidence becomes unknown instead of inventing an unplug event. A definite
connected EVSE state remains connected if CP is missing. Older layouts
without CP use known EVSE idle/connected states and leave fault states unknown.

## Controls

For availability, use the connection table above. Service controls follow the
app's **Read-only** setting; direct controls require **Modbus master**.

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `number.*_charging_current_limit` | Charging current limit | always | Slider: 0 A removes the energy-manager limit and bypasses extra current caps; 6 A or more sets a limit. Hardware limits still apply. While paused it shows the remembered setpoint; changing it can resume charging. |
| `switch.*_charging_paused` | Charging paused | always | On pauses charging by signalling 0 A; off resumes the stored setpoint. Uses the current command rather than the release relay command. |
| `switch.*_charging_release` | Charging release | always | Opens and closes the wallbox relay. Use the pause switch for everyday pausing; switching the relay wears it out. |
| `switch.*_wallbox_locked` | Wallbox locked | always | Locks the wallbox so it refuses to charge at all. |
| `select.*_requested_phases` | Requested phases | hardware that can switch phases | Single-phase or all-phase charging. A change while charging first pauses; resume separately after the waiting interval. Only exists on capable hardware. |

## Configuration

These controls change display behavior or perform maintenance.

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `select.*_solar_display_mode` | Solar display mode | always | The solar mode shown on the wallbox display. Drives the HMI only, and needs DIP 7 on. |
| `button.*_recover_from_error` | Recover from error | always | Manually retries heartbeat-error recovery when automatic recovery cannot complete. Preserves a deliberate pause, lock, disabled release and current setting. |
| `button.*_restart` | Restart | layout v01.03 | Restarts the wallbox. Refused unless the wallbox is idle. |

## Sensors

Version 0.5.2b2 adds **Pending change** (enum) and **Pending change due**
(timestamp), enabled by default.
The action states are `none`, `pause`, `resume`, `set_current`, `single_phase`,
`all_phases`, `enable_charging`, `disable_charging`, `unlock` and `lock`.
The due timestamp is empty when no change is queued or while a phase change
waits for the vehicle to confirm its pause. In that second case, the phase action
remains visible. Once confirmed, the timestamp shows the end of the configured
extra wait. A known timestamp stays fixed during a timed wait and can be displayed
as a remaining time without recording a changing number every second. Pause,
release, lock and
phase entities still report actual readback. The current slider shows the
remembered resume target while paused, which may be the pending target. See
[Waiting between charging changes](user-guide.md#waiting-between-charging-changes).

| Entity | Name | Unit | Class | Needs | Meaning |
|---|---|---|---|---|---|
| `sensor.*_pending_change` | Pending change |  | enum | 0.5.2b2; new service for service-owned queues | The queued action, or `none` when no action is reported. Enabled by default. Older services do not report this feature. |
| `sensor.*_pending_change_due` | Pending change due |  | timestamp | 0.5.2b2; new service for service-owned queues | Planned execution time when known. Empty with no pending action or while awaiting vehicle pause confirmation; afterward shows the end of the extra wait. Enabled by default, with no per-second countdown writes. |
| `sensor.*_evse_state` | EVSE state |  | enum | always | What the wallbox itself thinks it is doing. The main state sensor. |
| `sensor.*_authorization` | Authorization |  | enum | always | Whether RFID or the energy manager has released this session. |
| `sensor.*_wallbox_phase_selection` | Wallbox phase selection |  | enum | layout v01.02 | Which phases the wallbox will use when it next closes the relay. |
| `sensor.*_detected_vehicle_phases` | Detected vehicle phases |  | enum | layout v01.02 | How many phases the vehicle used in this session. |
| `sensor.*_signaled_current` | Signaled current | A | current | layout v01.03 | What the wallbox offers the vehicle on the pilot line. This is the number that should follow your charging-current limit. |
| `sensor.*_current_l1` | Current L1 | A | current | always | RMS output current on L1. |
| `sensor.*_current_l2` | Current L2 | A | current | always | RMS output current on L2. |
| `sensor.*_current_l3` | Current L3 | A | current | always | RMS output current on L3. |
| `sensor.*_voltage_l1` | Voltage L1 | V | voltage | always | RMS output voltage on L1. Disabled by default for new installations. |
| `sensor.*_voltage_l2` | Voltage L2 | V | voltage | always | RMS output voltage on L2. Disabled by default for new installations. |
| `sensor.*_voltage_l3` | Voltage L3 | V | voltage | always | RMS output voltage on L3. Disabled by default for new installations. |
| `sensor.*_power_l1` | Power L1 | W | power | always | Active power on L1. Disabled by default for new installations. |
| `sensor.*_power_l2` | Power L2 | W | power | always | Active power on L2. Disabled by default for new installations. |
| `sensor.*_power_l3` | Power L3 | W | power | always | Active power on L3. Disabled by default for new installations. |
| `sensor.*_total_power` | Total power | W | power | always | Active power on all phases. |
| `sensor.*_session_maximum_current` | Session maximum current | A | current | always | The highest current this session may use, after every static limit the wallbox applies. |
| `sensor.*_session_energy` | Session energy | kWh | energy | always | Energy transferred in the current session. |
| `sensor.*_session_duration` | Session duration | s | duration | always | Length of the current session. Live/history value without new long-term statistics. |
| `sensor.*_total_energy` | Total energy | kWh | energy | layout v01.02 | Lifetime energy on the AC port. Usable as an Energy dashboard source; the manufacturer marks it as **not usable for billing**. |
| `sensor.*_charging_sessions` | Charging sessions |  |  | layout v01.02 | Lifetime number of charging sessions. |

## Diagnostic sensors

These sit under *Diagnostic* on the device page.

| Entity | Name | Unit | Needs | Meaning |
|---|---|---|---|---|
| `sensor.*_cp_state` | CP state |  | layout v01.02 | The control-pilot state between wallbox and vehicle. Use the current and power sensors to check actual consumption. |
| `sensor.*_error_code` | Error code |  | always | The active error, as a documented category. The raw number is in the `code` attribute. |
| `sensor.*_temperature` | Temperature | °C | layout v01.02 | Temperature inside the wallbox. |
| `sensor.*_modbus_layout_version` | Modbus layout version |  | always | The register layout the firmware implements. Decides which entities exist. Disabled by default. |
| `sensor.*_downgrade_current` | Downgrade current | A | always | The current limit the wallbox applies while a grid-operator downgrade is active. |
| `sensor.*_maximum_house_current` | Maximum house current | A | always | The installation limit set with DIP switches 6-8 on bank S2. |
| `sensor.*_maximum_wallbox_current` | Maximum wallbox current | A | always | The wallbox's own maximum, set during installation. The upper bound of the charging-current limit. |
| `sensor.*_energy_manager_fallback` | Energy manager fallback |  | layout v01.02 | What the wallbox does when the heartbeat stops: keep the last values, pause, or fall back to a fixed current. |
| `sensor.*_energy_manager_fallback_current` | Energy manager fallback current | A | layout v01.02 | The fallback current, and only that: empty when the register holds a mode instead. |
| `sensor.*_grid_imbalance_threshold` | Grid imbalance threshold | A | layout v01.02 | The imbalance threshold, when imbalance monitoring is on. |
| `sensor.*_sunshine_minimum_current` | Sunshine+ minimum current | A | layout v01.02 | Minimum current in Sunshine+ mode. |
| `sensor.*_phase_switching_pause` | Phase switching pause | s | layout v01.02 | How long the wallbox pauses when it switches between one and three phases. |
| `sensor.*_phase_rotation` | Phase rotation |  | always | The order of the connected phases, which matters for load management. |
| `sensor.*_cable_lock` | Cable lock |  | always | Cable locking. A 4You 310 has a fixed cable and reports `fixed_cable`. |
| `sensor.*_phase_switching_mode` | Phase switching mode |  | always | How the wallbox's internal solar algorithm may use phases. |
| `sensor.*_connected_grid_phases` | Connected grid phases |  | layout v01.02 | How many grid phases are wired to the wallbox. |

## Binary sensors

| Entity | Name | Class | Needs | Meaning |
|---|---|---|---|---|
| `binary_sensor.*_vehicle_connected` | Vehicle connected | plug | always | EVSE and CP confirm a connected vehicle, including during faults. Conflicting or insufficient status is unknown. |
| `binary_sensor.*_charging` | Charging | battery_charging | always | The wallbox reports the charging state. |
| `binary_sensor.*_downgrade_active` | Downgrade active |  | always | The grid operator's downgrade input is active and the wallbox is limiting the current. |
| `binary_sensor.*_error` | Error | problem | always | An error code other than zero is active. |
| `binary_sensor.*_heartbeat` | Heartbeat | problem | direct master mode or service connection | OK after a confirmed heartbeat; problem when no heartbeat is confirmed within ten seconds or an active sender stops. Unknown before a result is available or when HA cannot obtain service status. Records health changes, not individual heartbeats. |
| `binary_sensor.*_energy_manager_fallback_active` | Energy manager fallback active | problem | layout v01.02 | The wallbox has fallen back because the energy manager stopped answering. |
| `binary_sensor.*_authorization_enabled` | Authorization enabled |  | layout v01.02 | RFID authorization is switched on in the wallbox. |
| `binary_sensor.*_grid_imbalance_monitoring` | Grid imbalance monitoring |  | layout v01.02 | Imbalance monitoring is switched on. |
| `binary_sensor.*_permanent_cable_lock` | Permanent cable lock |  | layout v01.02 | Permanent cable locking is switched on. |
| `binary_sensor.*_phase_switching_supported` | Phase switching supported |  | layout v01.01 | The hardware can switch between one and three phases. |

## Command events — next beta, unreleased

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `event.*_control_result` | Control result | direct connection, or a service supporting command events | Last observed command outcome, with `event_type`, `action` and `reason` attributes. Disabled by default; enable for automations when needed. |

The timestamp is when HA received the event. `executed` means the command was
accepted by the controller, not that the vehicle has started charging. Use
**Charging** and actual power to check that. A queued request creates its result
when it runs, is replaced, or is cancelled; the pending sensors show the wait.

| Event type | Meaning |
|---|---|
| `executed` | The controller accepted the command. |
| `replaced` | A newer request replaced a pending action. |
| `cancelled` | Pending work was cancelled when its controller stopped. |
| `failed` | The command failed, including a phase-pause timeout. |
| `uncertain` | A write may have reached the wallbox but could not be confirmed. Inspect readback before retrying. |
| `recovery_succeeded` | Fresh status confirmed error code 0 after recovery commands. |
| `recovery_failed` | A recovery write was rejected, or fresh status still showed an error after the confirmation window. |

The feed covers current, pause/resume, phase selection, release, locking and
error recovery. Solar display selection and restart do not publish command
outcomes.

This is a live notification feed, not a durable audit log. It keeps at most 32
outcomes in service memory. Startup, reconnect and service restart establish a
new baseline without replaying historical outcomes. Heartbeats, polling and
countdown ticks create no events. Older compatible services keep working;
the event entity stays unavailable until their service supports this feature.
See the [notification example](automations.md#react-to-command-results--next-beta-unreleased).

## Enumerated states

What the enum entities can report. The option names are what automations
and templates see.

| Entity | States |
|---|---|
| `sensor.*_evse_state` | `charging`, `error`, `ev_connected`, `idle`, `not_initialized`, `preconditions_valid`, `ready_to_charge`, `service_mode` |
| `sensor.*_cp_state` | `a1`, `a2`, `b1`, `b2`, `c1`, `c2`, `d1`, `d2`, `e`, `f`, `init` |
| `sensor.*_authorization` | `authorized`, `not_authorized`, `not_used` |
| `sensor.*_error_code` | `connected_phases_mismatch`, `energy_manager_unavailable`, `ev_overcurrent`, `no_error`, `other`, `voltage_out_of_range` |
| `sensor.*_detected_vehicle_phases` | `not_initialized`, `one_phase`, `three_phases`, `two_phases` |
| `sensor.*_energy_manager_fallback` | `disabled`, `fallback_current`, `pause_on_timeout` |
| `sensor.*_phase_rotation` | `l1_l2_l3`, `l2_l3_l1`, `l3_l1_l2` |
| `sensor.*_wallbox_phase_selection` | `all_available`, `single_phase` |
| `sensor.*_cable_lock` | `fixed_cable`, `locked`, `undetermined`, `unlocked` |
| `sensor.*_phase_switching_mode` | `solar_dynamic`, `solar_one_phase`, `solar_three_phases` |
| `sensor.*_connected_grid_phases` | `l1`, `l1_l2_l3` |
| `select.*_solar_display_mode` | `fast`, `not_active`, `sunshine`, `sunshine_plus` |
| `select.*_requested_phases` | `all_available`, `single_phase` |

## Actions

`mennekes_amtron_rtu.set_charging_current` writes the charging-current limit.
Both the action and the slider accept 0 A to remove the limit. See
[user-guide.md](user-guide.md#actions).
