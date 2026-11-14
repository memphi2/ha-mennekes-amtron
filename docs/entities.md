# Entity reference

Use **Charging current limit** and **Charging paused** for everyday charging.
**Total power**, **Total energy** and **Connection quality** show power use,
accumulated energy and connection health, including the heartbeat. The [User guide](user-guide.md) explains how to
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

## Device-page grouping

Daily charging controls stay in **Controls**. The display-only solar selector,
recovery and restart buttons belong to **Configuration**, as does the
**Virtual Plug & Charge** activation switch. Connection quality with
heartbeat health, internal temperature, the cable lock and the installation
settings belong to **Diagnostic**. The general **Error** sensor stays in the
main status section, with the raw code and its category as attributes.

**Requested phases** is your command; its `switched` attribute describes which
phases the next relay closure will use, while **Detected vehicle phases**
describes the vehicle's session. These values can legitimately differ during
a pending change.

Status-dependent icons distinguish paused/unpaused, locked/unlocked and
healthy/faulted states. An inactive pause does not claim that a car is charging.
Existing entity IDs, custom names and user-selected icons are retained.

**Vehicle connected** uses EVSE and CP status together. A wallbox error is not
a disconnect: a valid connected CP signal keeps it on. Insufficient or conflicting
evidence becomes unknown instead of inventing an unplug event. A definite
connected EVSE state remains connected if CP is missing. Older layouts
without CP use known EVSE idle/connected states and leave fault states unknown.

## New controls and status

**Charging status** explains observed state: initialization, a wallbox fault,
service mode, no connected vehicle, charging, a lock, disabled release, pause,
missing authorization, preparation or waiting for the vehicle. Faults take
priority; for a connected vehicle, lock, release, pause and authorization are
checked in that order. Missing or contradictory evidence is unknown. A queued
request is shown by **Pending change**, not presented as an executed change.
The status does not infer a full battery or identify an undocumented restriction.

**Connection quality** describes Modbus communication, including heartbeat and
write responses. It becomes degraded after a transaction that finally failed: a timeout or
malformed response that no resend made good, or a heartbeat that missed its
10-second deadline. A lost reply recovered by a resend does not degrade it. A
valid device error reply still proves communication. It returns to good after 300 seconds without
a communication error and a new confirmed response. Before the first response
it is unknown; loss of the connection is still handled by normal availability.
The attributes **Failed transactions** and **Retried transactions** count those
failures and the recovered lost replies since the last start of HA (direct
connection) or the app (service mode). They change only on those events, so a
healthy bus records nothing.

The **Virtual Plug & Charge** status sensor is optional. Supported direct
connections and services
provide the switch and status sensor even while the feature is off; the sensor
then shows **Disabled**. Enabling requires write access. An already enabled gate
can still be switched off in read-only mode; this does not resume charging. When the feature is
enabled, the sensor shows whether vehicle detection, profile
permission or charging release is still pending, whether another control blocks
release, or whether the observed connection is authorized. Authorization does
not mean that the vehicle is drawing power. The sensor has no per-poll timestamps
or session-token attributes. See [setup and status help](vehicle-setup.md#optional-virtual-plug--charge).

## Charging control at a glance

With the Modbus service app, three sensors summarize what the app's own
controllers are doing. Home Assistant only shows the app's summary; it decides
nothing from it. They are absent with a direct serial or LAN connection.

**Charging control** names who decides:

| State | Meaning |
|---|---|
| Guest order, Charging job | An [order from the app](app-guide.md#charging-jobs) holds the wallbox until it ends and the wallbox confirms its last pause, or until the car is unplugged. |
| Departure planning | A [departure](departure-planning.md#with-the-modbus-service-app) is set and not finished. |
| Direct control | No plan or order: your switches, numbers, targets and automations apply. |
| Wallbox unavailable | The app has no current reading. |

**Charging wait reason** names the first thing that keeps the car from
charging: no car, a switched-off release, Virtual Plug & Charge, then the
controller's own reason (for example **Paused until the planned start**), then
a reached target, a queued change or a manual pause. An order comes before a
departure plan, so the plan's status **An order from the app has priority**
appears here as the order's reason.

**Next charging step** is the earliest of the controller's next event and a
queued change. While the plan charges, **Target expected** carries the
[forecast](departure-planning.md#with-the-modbus-service-app); otherwise the
departure itself is the next step.

A standard card shows all three translated:

```yaml
type: entities
title: Wallbox
entities:
  - sensor.amtron_charging_control
  - sensor.amtron_charging_wait_reason
  - sensor.amtron_next_charging_step
  - sensor.amtron_charging_status
```

With the third-party [Mushroom](https://github.com/piitaya/lovelace-mushroom)
cards, one template card tells the whole story. Replace `amtron` with your
device's entity prefix:

```yaml
type: custom:mushroom-template-card
entity: sensor.amtron_charging_control
icon: mdi:ev-station
icon_color: >-
  {{ 'green' if is_state('sensor.amtron_charging_wait_reason', 'none')
     else 'red' if is_state('sensor.amtron_charging_control', 'unavailable')
     else 'orange' }}
primary: "{{ state_translated('sensor.amtron_charging_control') }}"
secondary: >-
  {{ state_translated('sensor.amtron_charging_wait_reason') }}
  {%- set at = state_attr('sensor.amtron_next_charging_step', 'at') %}
  {%- if at %}

  {{ state_translated('sensor.amtron_next_charging_step') }}:
  {{ as_timestamp(at) | timestamp_custom('%a %H:%M') }}
  {%- endif %}
  {%- set value = state_attr('sensor.amtron_charging_control', 'target_value') %}
  {%- if value %}

  {{ value | round(1) }}
  {{- ' %' if is_state_attr('sensor.amtron_charging_control', 'target', 'level') else ' kWh' }}
  {%- endif %}
multiline_secondary: true
tap_action:
  action: more-info
```

## Controls

For availability, use the connection table above. Service controls follow the
app's **Read-only** setting; direct controls require **Modbus master**.
**Active vehicle** changes the integration's selected profile and remains usable
in read-only mode; it does not send a charging command.

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `select.*_active_vehicle` | Active vehicle | optional vehicle profiles configured | Choose a car or no vehicle manually; either choice disables automatic selection. With detection sources configured, Automatic selection uses cable and home signals. Switching clears the previous percentage target and departure plan without resuming; settings and loss averages remain separate. |
| `number.*_vehicle_charge_target` | Vehicle charge target | optional configured vehicle SoC source; compatible service in service mode | Percentage ceiling, 0 = off. Only limits charging. Up to 100%, or the separately optional vehicle charge-limit source. Neither setting nor disabling resumes charging. |
| `datetime.*_next_departure` | Next departure | Departure planning enabled, vehicle SoC source and usable battery capacity | One dated target for the next trip. A timed goal may resume a pause to meet this time; the ordinary percentage goal alone never does. After reaching the target, the time remains available for climate until departure, then clears. |
| `button.*_use_next_calendar_event` | Use next calendar event | optional calendar and exact event title | Copies one matching timed event from the next 14 days into Next departure. Later events are not selected automatically. |
| `button.*_use_vehicle_departure` | Use vehicle departure | optional selected vehicle provider and enabled timer sensors | Copies one upcoming active climate timer from locally published vehicle states. Currently implemented for BavarianData; recurring timers need explicitly configured weekdays. No cloud fetch or recurring HA schedule. Existing installations retain their earlier entity ID. |
| `button.*_clear_departure_time` | Clear departure time | Departure planning enabled | Cancels the timed plan without changing the percentage ceiling or current wallbox state. |
| `number.*_energy_target` | Energy target | lifetime energy counter; compatible service in service mode | Additional kWh from the time you set it, in 0.1 kWh steps, up to 1000. Setting a positive value never starts or resumes charging. Zero disables the goal without resuming. |
| `button.*_cancel_pending_change` | Cancel pending change | new direct controller or supporting service | Removes the queued action without a wallbox write. Already executed preparation, such as a phase-change pause, remains. Available only while an action is pending. |
| `number.*_charging_current_limit` | Charging current limit | always | Slider: 0 A removes the energy-manager limit and bypasses extra current caps; 6 A or more sets a limit. Hardware limits still apply. While paused it shows the remembered setpoint; changing it can resume charging. |
| `switch.*_charging_paused` | Charging paused | always | On pauses charging by signalling 0 A; off resumes the stored setpoint. Uses the current command rather than the release relay command. |
| `switch.*_charging_release` | Charging release | always | Opens and closes the wallbox relay. Use the pause switch for everyday pausing; switching the relay wears it out. With Virtual Plug & Charge enabled, switching it on releases the current or next connection by hand for any car until the next unplug; switching it off stops the current connection, also for a recognized car, until the next unplug. |
| `switch.*_wallbox_locked` | Wallbox locked | always | Locks the wallbox so it refuses to charge at all. |
| `select.*_requested_phases` | Requested phases | hardware that can switch phases | Single-phase or all-phase charging. A change while charging first pauses; resume separately after the waiting interval. Only exists on capable hardware. The `switched` attribute is the wallbox's answer: the phases it will use when it next closes the relay. |

## Configuration

These controls configure charging permission, display behavior or maintenance.

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `switch.*_virtual_plug_charge` | Virtual Plug & Charge | supported direct control or service capability | Enables the wallbox-wide permission check for every profile with detection signals. Enabling can stop a charge while recognition is pending; disabling never resumes charging. |
| `switch.*_switch_off_charging_release_when_unplugged` | Switch off charging release when unplugged | write access | Off by default. After a confirmed disconnect, Home Assistant switches **Charging release** off so the next car needs an explicit release. It also ends a manual **Charging paused**, so the wallbox no longer shows a pause without a car; a stop from a reached energy target stays. Ignored while Virtual Plug & Charge is enabled, which already closes the release; switching both on adds nothing. Not enforced while HA is not running. |
| `select.*_solar_display_mode` | Solar display mode | always | The solar mode shown on the wallbox display. Drives the HMI only, and needs DIP 7 on. |
| `button.*_recover_from_error` | Recover from error | always | Manually retries heartbeat-error recovery when automatic recovery cannot complete. Preserves a deliberate pause, lock, disabled release and current setting. |
| `button.*_restart` | Restart | layout v01.03 | Restarts the wallbox. Refused unless the wallbox is idle. |

## Sensors

Each sensor answers one question. A register that only details or explains
another sensor's state is an attribute of that sensor: the phase voltages ride
on **Total power**, the session counters on **Session energy**, the
installation settings on **Wallbox settings**. Attributes are visible in the
entity's *Attributes* panel and reachable with `state_attr()` in templates. All
sensors are enabled by default.

**Pending change** (enum) carries its deadline as the `due` attribute. The
action states are `none`, `pause`, `resume`, `set_current`, `single_phase`,
`all_phases`, `enable_charging`, `disable_charging`, `unlock` and `lock`.
`due` is absent when no change is queued or while a phase change waits for the
vehicle to confirm its pause. In that second case, the phase action remains
visible. Once confirmed, the attribute shows the end of the configured extra
wait. A known deadline stays fixed during a timed wait and can be displayed as
a remaining time without recording a changing number every second. Pause,
release, lock and phase entities still report actual readback. The current
slider shows the remembered resume target while paused, which may be the
pending target. See
[Waiting between charging changes](user-guide.md#waiting-between-charging-changes).

| Entity | Name | Unit | Class | Needs | Meaning |
|---|---|---|---|---|---|
| `sensor.*_vehicle_charge_target_status` | Vehicle charge target status | — | enum | optional configured vehicle SoC source | `disabled`, `active`, `reached`. Missing SoC leaves charging unchanged; reached stays latched until a new target or disabling. A reached target pauses only with a car at the wallbox. |
| `sensor.*_timed_charging_status` | Timed charging status | — | enum | Departure planning enabled | Stable plan status. No per-minute countdown is recorded. The deadline is shown by Next departure; the `planned_start` attribute holds when the plan starts charging for the next trip, or when it started, and is absent while the plan lacks its inputs or the trip is done. With the Modbus service app the app plans and this sensor shows its state, including **Charged to the estimated target** and **An order from the app has priority**. **Charging to the minimum level** means the car is below its [minimum charge level](departure-planning.md#minimum-charge-level) and charges before the planned start. With the app, the attributes `estimated_finish`, `estimated_level_at_departure`, `learned_efficiency` and `measured_power_kw` carry its [forecast](departure-planning.md#with-the-modbus-service-app). |
| `sensor.*_charging_control` | Charging control | — | enum | Modbus service app | Which of the app's controllers decides about the charge right now; see [Charging control at a glance](#charging-control-at-a-glance). The attributes `vehicle`, `target`, `target_value` and `target_deadline` name the car's state and what is charged towards. |
| `sensor.*_charging_wait_reason` | Charging wait reason | — | enum | Modbus service app | Why the car does not charge yet, or **Nothing is holding the charge**. It names the first reason that applies, not every one. |
| `sensor.*_next_charging_step` | Next charging step | — | enum | Modbus service app | What the app expects next; the `at` attribute holds its time. No countdown is recorded. |
| `sensor.*_charging_job` | Charging job | — | enum | Modbus service app | State of the app's charging job or guest charge, or **No job**; the attributes hold its window, current, energy and progress. See [Use the app from Home Assistant](app-from-home-assistant.md). |
| `sensor.*_virtual_plug_charge_status` | Virtual Plug & Charge status | — | enum | supported direct/service capability | Shows recognition, permission and release status, or Disabled. **Charging blocked** carries a `blocked_reason` attribute (for example `release_off`, `wallbox_locked`, `authorization_required`, `pause_requested`, `energy_goal_stop` or `register_unavailable`). **Released by hand** shows a manual release for the current or next connection. No session tokens or ticking timestamps are recorded. |
| `sensor.*_charging_status` | Charging status |  | enum | always | Explains verified charging state or a known blocking setting. The raw wallbox states behind it are the `evse_state` and `cp_state` attributes (see [Attribute values](#attribute-values)). |
| `sensor.*_energy_target_status` | Energy target status |  | enum | lifetime energy counter | `disabled`, `active`, `reached`, `meter_unavailable` or `meter_reset`. Reached means the energy threshold was observed, not that a pause write has already been confirmed. |
| `sensor.*_pending_change` | Pending change |  | enum | always | The queued action, or `none` when no action is reported, with the planned execution time as the `due` attribute. |
| `sensor.*_authorization` | Authorization |  | enum | always | Whether RFID or the energy manager has released this session. The `enabled` attribute (layout v01.02) tells whether RFID authorization is switched on in the wallbox at all. |
| `sensor.*_detected_vehicle_phases` | Detected vehicle phases |  | enum | layout v01.02 | How many phases the vehicle used in this session. The phases the wallbox itself will use are the `switched` attribute of **Requested phases**. |
| `sensor.*_signaled_current` | Signaled current | A | current | layout v01.03 | What the wallbox offers the vehicle on the pilot line. This is the number that should follow your charging-current limit. |
| `sensor.*_total_power` | Total power | W | power | always | Active power on all phases. While power flows, the phase details ride along: the RMS currents as `current_l1`, `current_l2` and `current_l3` (tenths of an ampere), the RMS voltages as `voltage_l1`, `voltage_l2` and `voltage_l3` (whole volts) and the active power per phase as `power_l1`, `power_l2` and `power_l3` (whole watts). An idle wallbox reports none, so a wobbling mains voltage writes no history. |
| `sensor.*_session_energy` | Session energy | kWh | energy | always | Energy transferred in the current session. Attributes: `duration_minutes` (length of the session, in minutes, so the attribute changes once a minute rather than once a second), `maximum_current` (the highest current this session may use after every static limit, A) and `sessions` (lifetime number of charging sessions, layout v01.02). |
| `sensor.*_total_energy` | Total energy | kWh | energy | layout v01.02 | Lifetime energy on the AC port. Usable as an Energy dashboard source; the manufacturer marks it as **not usable for billing**. |

## Diagnostic sensors

These sit under *Diagnostic* on the device page.

| Entity | Name | Unit | Needs | Meaning |
|---|---|---|---|---|
| `sensor.*_connection_quality` | Connection quality |  | direct connection or supporting service | `good` or `degraded`; a stable communication-health summary. Attribute `heartbeat` is `ok` after a confirmed heartbeat and `problem` when no heartbeat is confirmed within ten seconds or an active sender stops; it is absent without a sender (read-only entries) or before a result is available. `failed_transactions` counts transactions that finally failed since start, including a heartbeat that missed its 10-second deadline; only these degrade the quality. `retried_transactions` counts lost replies that a resend made good: a read retry or a heartbeat resent after one second. All three change only on those events, so individual heartbeats are never recorded. |
| `sensor.*_temperature` | Temperature | °C | layout v01.02 | Temperature inside the wallbox. |
| `sensor.*_cable_lock` | Cable lock |  | always | Cable locking. A 4You 310 has a fixed cable and reports `fixed_cable`. The `permanent` attribute (layout v01.02) tells whether permanent cable locking is switched on. |
| `sensor.*_wallbox_settings` | Wallbox settings |  | always | The installation, read from the wallbox: the state is the register layout the firmware implements (`v01.00` … `v01.03`), which decides which entities exist. The attributes are what the installer set with DIP switches and the service tool, so they change only when someone opens the wallbox: `max_current_house` (the installation limit, DIP switches 6-8 on bank S2, A), `max_evse_current` (the wallbox's own maximum and the upper bound of the charging-current limit, A), `phase_rotation`, `grid_phases_connected` (layout v01.02), `phase_options` (whether the hardware can switch between one and three phases, layout v01.01), `phase_switching_mode` (how the internal solar algorithm may use phases), `phase_switching_pause_seconds` (how long the wallbox pauses when it switches phases, layout v01.02), `grid_imbalance_monitoring` and `grid_imbalance_threshold` (A, layout v01.02) and `solar_min_current` (the Sunshine+ minimum current, A, layout v01.02). |

## Binary sensors

| Entity | Name | Class | Needs | Meaning |
|---|---|---|---|---|
| `binary_sensor.*_vehicle_connected` | Vehicle connected | plug | always | EVSE and CP confirm a connected vehicle, including during faults. Conflicting or insufficient status is unknown. |
| `binary_sensor.*_charging` | Charging | battery_charging | always | The wallbox reports the charging state. |
| `binary_sensor.*_downgrade_active` | Downgrade active |  | always | The grid operator's downgrade input is active and the wallbox is limiting the current. The limit it applies while downgraded is the `current` attribute (A). |
| `binary_sensor.*_error` | Error | problem | always | An error code other than zero is active. The raw number is the `code` attribute, its documented category the `category` attribute (see [Attribute values](#attribute-values)); both are also carried by the `error_occurred` wallbox event. |
| `binary_sensor.*_energy_manager_fallback_active` | Energy manager fallback active | problem | layout v01.02 | The wallbox has fallen back because the energy manager stopped answering. What it does then is the `behaviour` attribute: keep the last values (`disabled`), pause (`pause_on_timeout`) or fall back to a fixed current (`fallback_current`, with the amperes in the `fallback_current` attribute, which is absent when the register holds a mode instead). |

## Command events

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `event.*_control_result` | Control result | direct connection, or a service supporting command events | Last observed command and its outcome, with `event_type`, `action`, `result` and `reason` attributes. Disabled by default; enable for automations when needed. |

The event type names the command and its result, for example `pause_executed`
or `set_current_failed`, so HA's history and activity show which command it
was. Error recovery reports `recovery_succeeded`, `recovery_failed` or
`recovery_uncertain`. The `action` attribute holds the command and `result`
the outcome alone; filter on `result` to react to every failed command.

The timestamp is when HA received the event. `executed` means the command was
accepted by the controller, not that the vehicle has started charging. Use
**Charging** and actual power to check that. A queued request creates its result
when it runs, is replaced, or is cancelled; the pending sensors show the wait.

| Result | Meaning |
|---|---|
| `executed` | The controller accepted the command. |
| `replaced` | A newer request replaced a pending action. |
| `cancelled` | Pending work was cancelled by the user, an energy stop, or controller shutdown. |
| `failed` | The command failed, including a phase-pause timeout. |
| `uncertain` | A write may have reached the wallbox but could not be confirmed. Inspect readback before retrying. |
| `recovery_succeeded` | Fresh status confirmed error code 0 after recovery commands. |
| `recovery_failed` | A recovery write was rejected, or fresh status still showed an error after the confirmation window. |

The feed covers current, pause/resume, phase selection, release, locking and
error recovery. Cancelling a queued action reports `cancelled` with reason
`user_cancelled`; a kWh or percentage goal can cancel queued work with reason
`energy_goal`. When a charging job of the app cancels queued work or refuses a
queued command, the reason is `service_job`. Solar display selection and restart do not publish command
outcomes.

This is a live notification feed, not a durable audit log. It keeps at most 32
outcomes in service memory. Startup, reconnect and service restart establish a
new baseline without replaying historical outcomes. Heartbeats, polling and
countdown ticks create no events. See the [notification example](automations.md#react-to-command-results).

The [energy-target guide](user-guide.md#energy-target)
explains restart behavior, measurement failures and stopping accuracy.

## Wallbox events

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `event.*_wallbox_event` | Wallbox event | direct connection or service | A change the wallbox itself reported between two polls, with `event_type` and the attributes of that moment. Enabled by default. |

**Control result** tells you what your commands did; **Wallbox event** tells
you what the wallbox did on its own: a car plugged in, a charge that ended, a
fault, an RFID decision. Use it for notifications and for automations that
react to the car rather than to your own commands.

| Event type | When | Attributes |
|---|---|---|
| `vehicle_plugged_in` | A car is connected (EVSE and control-pilot state). | `cp_state` |
| `vehicle_unplugged` | The car is gone. | `cp_state`, `session_energy` (kWh) and `session_duration` (s) last seen with the car |
| `charging_started` | The wallbox state became **charging**. | `evse_state` |
| `charging_stopped` | The wallbox left **charging**, for a pause, a reached target or an unplug. | `evse_state`, `session_energy`, `session_duration` |
| `error_occurred` | The error code became non-zero. | `error_code`, `error_category` |
| `error_cleared` | The error code returned to 0. | the code and category that were cleared |
| `authorized` | RFID or the energy manager authorized the connection. | `authorization` |
| `authorization_rejected` | The authorization was refused. | `authorization` |
| `downgrade_started` | The external downgrade input became active. | `downgrade_current` (A) |
| `downgrade_ended` | The downgrade input released. | |
| `cable_locked` / `cable_unlocked` | The cable lock status changed between locked and unlocked; a fixed cable never reports this. | `cable_lock` |
| `phases_switched` | The wallbox changed the phases it uses when it closes the relay. | `phases`: `all_available` or `single_phase` |

Attribute values are the same stable options as the matching sensors, so they
work in every UI language. A charge that ends with the unplug reports
`charging_stopped` first, then `vehicle_unplugged`.

Only a change between two known readings creates an event. The first reading
after HA starts, after a reload, after a lost connection or after a failed poll
is a baseline: nothing is reported for it, and a change that happened in the
gap is not reported at an invented time. A reading that is briefly unknown,
for example the vehicle presence during a wallbox fault, keeps its last known
baseline, so a fault that clears does not invent a plug-in. A change that flips
back within one poll interval is not seen. Measurements, counters,
configuration registers and your own commands never create wallbox events.
See the [notification example](automations.md#react-to-wallbox-events).

## Enumerated states

What the enum entities can report. The option names are what automations
and templates see.

| Entity | States |
|---|---|
| `sensor.*_authorization` | `authorized`, `not_authorized`, `not_used` |
| `sensor.*_detected_vehicle_phases` | `not_initialized`, `one_phase`, `three_phases`, `two_phases` |
| `sensor.*_cable_lock` | `fixed_cable`, `locked`, `undetermined`, `unlocked` |
| `select.*_solar_display_mode` | `fast`, `not_active`, `sunshine`, `sunshine_plus` |
| `select.*_requested_phases` | `all_available`, `single_phase` |
| `sensor.*_charging_control` | `unavailable`, `guest_order`, `charging_job`, `departure_plan`, `direct` |
| `sensor.*_charging_wait_reason` | `none`, `wallbox_unavailable`, `no_vehicle`, `release_off`, `plug_charge_waiting`, `until_job_start`, `meter_unavailable`, `job_suspended`, `job_ended`, `vehicle_unconfirmed`, `until_planned_start`, `estimated_target_reached`, `waiting_for_device`, `waiting_for_soc`, `waiting_for_capacity`, `waiting_for_target`, `read_only`, `command_pending`, `target_reached`, `paused` |
| `sensor.*_next_charging_step` | `none`, `queued_command`, `job_start`, `job_end`, `plan_start`, `target_reached`, `departure` |
| `sensor.*_charging_job` | `none`, `scheduled`, `charging`, `waiting_meter`, `suspended`, `reached`, `finished`, `stopped`, `interrupted` |

### Attribute values

Enumerated attributes use the same option names; an undocumented register
value leaves the attribute out.

| Attribute | Values |
|---|---|
| `evse_state` on **Charging status** | `charging`, `error`, `ev_connected`, `idle`, `not_initialized`, `preconditions_valid`, `ready_to_charge`, `service_mode` |
| `cp_state` on **Charging status** (layout v01.02) | `a1`, `a2`, `b1`, `b2`, `c1`, `c2`, `d1`, `d2`, `e`, `f`, `init` |
| `category` on **Error** | `connected_phases_mismatch`, `energy_manager_unavailable`, `ev_overcurrent`, `no_error`, `other`, `voltage_out_of_range` |
| `behaviour` on **Energy manager fallback active** | `disabled`, `fallback_current`, `pause_on_timeout` |
| `switched` on **Requested phases** | `all_available`, `single_phase` |
| `heartbeat` on **Connection quality** | `ok`, `problem` |
| `vehicle` on **Charging control** | `unknown`, `none`, `connected`, `confirmed` |
| `target` on **Charging control** | `level` (percent), `energy` (kWh); absent without a target |
| `phase_rotation` on **Wallbox settings** | `l1_l2_l3`, `l2_l3_l1`, `l3_l1_l2` |
| `grid_phases_connected` on **Wallbox settings** | `l1`, `l1_l2_l3` |
| `phase_options` on **Wallbox settings** | `one_phase_only`, `one_or_three_phases`, `three_phases_only` |
| `phase_switching_mode` on **Wallbox settings** | `solar_dynamic`, `solar_one_phase`, `solar_three_phases` |

## Actions

`mennekes_amtron_rtu.set_charging_current` writes the charging-current limit.
Both the action and the slider accept 0 A to remove the limit. See
[user-guide.md](user-guide.md#actions).

With the Modbus service app, six more actions start and stop the app's
charging jobs, boost and guest charging; see
[Use the app from Home Assistant](app-from-home-assistant.md).


## Optional charging estimates

| Entity | Name | Unit | Needs | Meaning |
|---|---|---|---|---|
| `sensor.*_estimated_charging_loss_last_charge` | Estimated charging loss, last charge | % | Configured vehicle energy source | Last matched connection cycle; unknown when the newest completed cycle cannot be matched. |
| `sensor.*_average_estimated_charging_loss` | Average estimated charging loss | % | Configured vehicle energy source | Energy-weighted average of accepted matches; `sample_count` counts accepted cycles. |
| `button.*_assign_vehicle_charge_energy` | Assign vehicle charge energy | | Vehicle energy sensor selected | Explicitly pair the current vehicle kWh value with the latest completed wallbox cycle while unplugged. Also works read-only. |
| `sensor.*_estimated_charging_cost_current_charge` | Estimated charging cost, current charge | EUR | Fixed or dynamic price configured; enable entity manually | Disabled by default to reduce Recorder writes. Running variable cost estimate; unknown for incomplete observations. |
| `sensor.*_estimated_charging_cost_last_charge` | Estimated charging cost, last charge | EUR | Fixed or dynamic price configured | Cost of the latest completed cycle, retained across restarts. |

All estimates require layout v01.02 or newer and completed-session support in
service mode. Costs additionally require timestamped service energy samples.
They have no state class and create no long-term statistics. Losses update per
match/session; costs are rounded to cents. The current-cost sensor exposes only
`complete`; the last-cost sensor also exposes `energy_kwh` and `unpriced_kwh`,
rounded to 0.01 kWh. Setup, units, §14a tariff handling,
matching limits and Recorder behavior are described in
[Charging losses and costs](charging-estimates.md).
