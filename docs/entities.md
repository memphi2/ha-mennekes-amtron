# Entity reference

[User guide](README.md) · [Dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Use **Charging current limit** and **Charging paused** for everyday charging.
**Charging status** says what the wallbox is doing, **Charging wait reason**
why the car does not charge yet. **Total power** and **Total energy** show power
use and accumulated energy. The [User guide](user-guide.md) explains how to use
these controls together; this page lists every entity and its state values.

The first column shows default **English** entity-ID patterns. `*` stands
for the device prefix; it is not part of a literal entity ID. For example,
Total energy normally becomes `sensor.mennekes_amtron_total_energy`.
Home Assistant derives IDs from translated names, not internal register keys.
Language, renaming and multiple devices can change them: copy your actual IDs
from **Settings → Devices & services → Entities** before using YAML examples.

Every entry runs the same wallbox service: a direct USB or LAN entry runs it
inside Home Assistant, an app entry reaches the Modbus service app. Both
provide the same entities, controls, targets and events.

The **Needs** column says what the wallbox or your configuration has to
provide. A register that a firmware does not have produces no entity at all,
so fewer entities on an older layout can be expected. Diagnostic entities
that are disabled by default need enabling on their entity settings page. A
temporarily failed read makes an existing entity unavailable until
communication recovers.

| Connection | When controls are available |
|---|---|
| **Direct USB or LAN** | Select **Control mode → Modbus master** in the integration options. A read-only entry provides readings and events without adding controls. |
| **Modbus service app** | Disable **Read-only** in the app configuration, save and restart the app. Controls become available after fresh service status arrives; HA needs no restart for this switch. Their IDs stay registered while the service is read-only, disconnected or not ready. |

Two state names mean the same thing on every entity and attribute:
`disabled` means switched off by a setting; `none` means nothing applies
right now.

## Device-page grouping

Daily charging controls stay in **Controls**. The recovery and restart
buttons, the **Virtual Plug & Charge** switch and **Active vehicle** belong to
**Configuration**. Connection quality, internal temperature, authorization,
the cable lock, the detected vehicle phases and the installation settings
belong to **Diagnostic**. The general **Error** sensor stays in the main
status section, with the raw code and its category as attributes.

**Requested phases** is your command; its `switched` attribute describes which
phases the next relay closure will use, while **Detected vehicle phases**
describes the vehicle's session. These values can legitimately differ during
a pending change.

Status-dependent icons distinguish paused/unpaused, locked/unlocked and
healthy/faulted states. An inactive pause does not claim that a car is charging.
Custom names and user-selected icons are retained.

**Vehicle connected** uses EVSE and CP status together. A wallbox error is not
a disconnect: a valid connected CP signal keeps it on. Insufficient or conflicting
evidence becomes unknown instead of inventing an unplug event. A definite
connected EVSE state remains connected if CP is missing. Older layouts
without CP use known EVSE idle/connected states and leave fault states unknown.

## Charging status

**Charging status** explains the observed state: initialization, a wallbox
fault, service mode, no connected vehicle, charging, a lock, a disabled
release, a pause, missing authorization, preparation or waiting for the
vehicle. Faults take priority; for a connected vehicle, lock, release, pause
and authorization are checked in that order. A queued request is not
presented as an executed change; **Charging wait reason** shows it. The status
does not infer a full battery or identify an undocumented restriction.

| State | Meaning |
|---|---|
| `initializing` | The wallbox is starting up. |
| `wallbox_error` | The wallbox reports an error code or its error state. See **Error**. |
| `service_mode` | The wallbox is in service mode. |
| `no_vehicle` | No car is connected. |
| `charging` | The wallbox charges and no readback contradicts it. |
| `locked` | **Wallbox locked** is on. |
| `release_disabled` | **Charging release** is off. |
| `paused` | **Charging paused** is on: the wallbox signals 0 A. |
| `authorization_required` | RFID or the energy manager has not authorized this connection. |
| `preparing` | Everything on the wallbox side allows charging; the wallbox prepares the charge. |
| `waiting_for_vehicle` | The wallbox is ready; the car does not draw current yet, for example because it is full or its own timer waits. |
| `checking_status` | The readings do not fit together yet. Brief ambiguity is neutral. |
| `state_uncertain` | The readings stayed contradictory for at least two polls and ten seconds. See [Charging status is being checked](troubleshooting.md#charging-status-is-being-checked). |
| `data_missing` | A reading needed for the status is missing. |

| Attribute | Meaning |
|---|---|
| `evse_state` | The raw wallbox state (see [Attribute values](#attribute-values)). |
| `cp_state` | The raw control-pilot state, layout v01.02. |
| `downgraded_to` | Only while the grid operator's downgrade input is active: the current the wallbox limits to, in A. |

## Charging wait reason

**Charging wait reason** names the first thing that keeps the car from
charging, or `none`. It names the first reason that applies, not every one.
Guest charging takes priority over a vehicle departure plan, including while
the guest is paused.

| State | Meaning |
|---|---|
| `none` | Nothing is holding the charge. |
| `wallbox_unavailable` | The service has no current reading of the wallbox. |
| `no_vehicle` | No car is connected. |
| `vehicle_unconfirmed` | A guest start or a plan waits for confirmation that this is the expected car. |
| `release_off` | **Charging release** is switched off. |
| `plug_charge_waiting` | Virtual Plug & Charge has not released the connected car yet. |
| `meter_unavailable` | The energy target cannot be checked: the energy meter is unavailable or its reading decreased. |
| `until_planned_start` | The departure plan pauses until its planned start. |
| `estimated_target_reached` | The departure plan charged to its estimated target. |
| `waiting_for_device` | The departure plan waits for the wallbox. |
| `waiting_for_soc` | The departure plan waits for the vehicle's charge level. |
| `waiting_for_capacity` | The departure plan waits for the battery capacity. |
| `waiting_for_target` | The departure plan waits for a percentage target. |
| `read_only` | The departure plan cannot act because writing is not allowed. |
| `target_reached` | An energy or vehicle charge target is reached. |
| `command_pending` | A change is queued and waits for its turn. |
| `paused` | Charging is paused by hand. |

Its attributes say who controls the charge, what comes next and what it aims
for. They hold fixed values only, never a ticking counter; an attribute that
does not apply is left out.

| Attribute | Meaning |
|---|---|
| `controller` | Who decides: `guest` (guest charging with the shared kWh and time limits), `departure_plan` (a [departure](departure-planning.md) is set and not finished), `direct` (your switches, numbers, targets and automations apply) or `unavailable` (no current reading). |
| `next_event`, `next_at` | What the service expects next and when: `none`, `queued_command` (a queued change runs), `plan_start` (the planned charge starts), `target_reached` (the plan's forecast end) or `departure` (the target time). No countdown is recorded. |
| `vehicle` | The car's state: `unknown`, `none`, `connected` or `confirmed` (the plan has confirmed it is the selected car). |
| `target`, `target_value`, `target_deadline` | What is charged towards: `level` (percent) or `energy` (kWh), its value and its time. |
| `pending`, `pending_at` | The queued change (`pause`, `resume`, `set_current`, `single_phase`, `all_phases`, `enable_charging`, `disable_charging`, `unlock`, `lock`, or `none`) and when it runs. See [Waiting between charging changes](user-guide.md#waiting-between-charging-changes). |
| `energy_target` | Status of **Energy target**: `disabled`, `active`, `reached`, `meter_unavailable` or `meter_reset`. Reached means the energy threshold was observed, not that a pause write has already been confirmed. Absent when the wallbox has no lifetime energy counter. |
| `vehicle_target` | Status of **Vehicle charge target**: `disabled`, `active` or `reached`. Reached stays latched until a new target or disabling. Absent without a vehicle charge-level source. |
| `departure_plan` | Status of the [departure plan](departure-planning.md), when departure planning is enabled for the selected car. See [Plan states](#departure-plan-states). |
| `planned_start` | When the plan starts charging for the next trip, or when it started. |
| `estimated_finish`, `estimated_level_at_departure` | The plan's [forecast](departure-planning.md). |
| `plug_charge` | Virtual Plug & Charge status, when the switch exists: `disabled`, `waiting_vehicle`, `waiting_detection`, `vehicle_not_selected`, `waiting_release`, `authorized`, `manual_release` (released by hand for the current or next connection) or `blocked`. Authorization does not mean that the vehicle is drawing power. |
| `plug_charge_blocked` | Why Virtual Plug & Charge is blocked: `connection_lost`, `gate_inactive`, `read_only`, `departure_plan`, `pause_requested`, `release_off`, `energy_goal_stop`, `wallbox_locked`, `error_code`, `authorization_required` or `register_unavailable`. |

### Departure plan states

| State | Meaning |
|---|---|
| `disabled` | No plan is active. |
| `waiting_for_target`, `waiting_for_capacity`, `waiting_for_soc`, `waiting_for_device` | The plan lacks an input. |
| `vehicle_unconfirmed` | The connected car is not confirmed as the selected car. |
| `read_only` | Writing is not allowed. |
| `guest_mode` | Guest charging has priority. |
| `queued`, `write_pending` | A change of the plan waits to be written. |
| `paused_until_start` | Paused until the planned start. |
| `minimum_level` | The car is below its [minimum charge level](departure-planning.md#minimum-charge-level) and charges before the planned start. |
| `charging` | Charging at full current. |
| `finishing` | Finishing at minimum current. |
| `unreachable` | The target may not be reached in time. |
| `late` | The departure time has passed. |
| `estimated_reached` | Charged to the estimated target. |
| `reached` | Target reached. |
| `error` | The plan failed. |

For a ready-made card, use the **[dashboard examples](app-from-home-assistant.md#dashboard-with-mushroom-cards)**.
They include a small built-in card and a larger Mushroom dashboard in five
languages, with the default `mennekes_amtron` prefix.

## Controls

For availability, use the connection table above. **Active vehicle** changes
the integration's selected profile and remains usable in read-only mode; it
does not send a charging command.

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `number.*_charging_current_limit` | Charging current limit | always | Slider: 0 A removes the energy-manager limit and bypasses extra current caps; 6 A or more sets a limit. Hardware limits still apply. While paused it shows the remembered setpoint; changing it can resume charging. |
| `switch.*_charging_paused` | Charging paused | always | On pauses charging by signalling 0 A; off resumes the stored setpoint. Uses the current command rather than the release relay command. |
| `switch.*_charging_release` | Charging release | always | Opens and closes the wallbox relay. Use the pause switch for everyday pausing; switching the relay wears it out. With Virtual Plug & Charge enabled, switching it on releases the current or next connection by hand for any car until the next unplug; switching it off stops the current connection, also for a recognized car, until the next unplug. |
| `switch.*_wallbox_locked` | Wallbox locked | always | Locks the wallbox so it refuses to charge at all. |
| `select.*_requested_phases` | Requested phases | hardware that can switch phases | `all_available` or `single_phase`. A change while charging first pauses; resume separately after the waiting interval. The `switched` attribute is the wallbox's answer: the phases it will use when it next closes the relay. |
| `select.*_solar_display_mode` | Solar display mode | always | The solar charging mode shown on the wallbox display and its solar LEDs. The energy manager runs the solar algorithm itself; the wallbox needs DIP 7 on for the mode to have any effect. Takes effect at once, without the state-change interval. |
| `number.*_energy_target` | Energy target | lifetime energy counter | Additional kWh from the time you set it, in 0.1 kWh steps, up to 1000. Setting a positive value never starts or resumes charging. Zero disables the target without resuming. Its status is the `energy_target` attribute of **Charging wait reason**. |
| `number.*_vehicle_charge_target` | Vehicle charge target | configured vehicle charge-level source | Percentage ceiling, 0 = off. Only limits charging. Up to 100%, or the separately optional vehicle charge-limit source. Neither setting nor disabling resumes charging. Its status is the `vehicle_target` attribute of **Charging wait reason**. |
| `datetime.*_target_time` | Target time | always | One shared dated target: guest stop time or the selected car's next departure. A departure plan may resume a pause to meet this time; the ordinary percentage target alone never does. After reaching the target, the time remains available for climate until departure, then clears. |
| `button.*_cancel_pending_change` | Cancel pending change | always | Removes the queued action without a wallbox write. Already executed preparation, such as a phase-change pause, remains. Available only while an action is pending. |
| `select.*_active_vehicle` | Active vehicle | vehicle profiles configured | Choose a car or no vehicle manually; either choice disables automatic selection. With detection sources configured, Automatic selection uses cable and home signals. Switching clears the previous percentage target and departure plan without resuming; settings and loss averages remain separate. |

## Guest charging and shared time

**Start guest charging** (`button.*_start_guest_charging`) starts the
connected guest immediately, using the ordinary kWh target and the shared
target time, if set. The first limit reached stops charging. Use the normal
pause to stop manually. **Guest charging** (`binary_sensor.*_guest_charging`)
is on while a guest charge is active.

**Target time** is shared: for a guest it means “stop no later than”, and for
a recognized vehicle it supplies the departure plan's deadline. Clear it with
`mennekes_amtron_rtu.clear_target_time`, targeting this datetime entity; the
Mushroom example attaches this to holding the time card. Other targets remain
unchanged. See the [dashboard and charging guide](app-from-home-assistant.md).

## Configuration

These controls configure charging permission or maintenance.

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `switch.*_virtual_plug_charge` | Virtual Plug & Charge | always | Enables the wallbox-wide permission check for every profile with detection signals. Enabling requires write access and can stop a charge while recognition is pending; disabling never resumes charging and stays possible in read-only mode. Its status is the `plug_charge` attribute of **Charging wait reason**. See [setup and status help](vehicle-setup.md#optional-virtual-plug--charge). |
| `button.*_recover_from_error` | Recover from error | always | Manually retries heartbeat-error recovery when automatic recovery cannot complete. Preserves a deliberate pause, lock, disabled release and current setting. |
| `button.*_restart` | Restart | layout v01.03 | Restarts the wallbox. Refused unless the wallbox is idle. |

**Switch off charging release when unplugged** is an integration option
(**Configure → Wallbox**, for a direct entry under **Current limit and polling**), off by default. After a confirmed disconnect,
the service that talks to the wallbox switches **Charging release** off so
the next car needs an explicit release. It also ends a manual **Charging
paused**, so the wallbox no longer shows a pause without a car; a stop from a
reached target stays. It is ignored while Virtual Plug & Charge is enabled,
which already closes the release. With the app, the rule also applies while
Home Assistant is not running.

## Sensors

Each sensor answers one question. A register that only details or explains
another sensor's state is an attribute of that sensor: the phase values ride
on **Total power**, the session counters on **Session energy**, the
installation settings on **Wallbox settings**. Attributes are visible in the
entity's *Attributes* panel and reachable with `state_attr()` in templates.

| Entity | Name | Unit | Class | Needs | Meaning |
|---|---|---|---|---|---|
| `sensor.*_total_power` | Total power | W | power | always | Active power on all phases. While power flows, the phase details ride along: the RMS currents as `current_l1`, `current_l2` and `current_l3` (tenths of an ampere), the RMS voltages as `voltage_l1`, `voltage_l2` and `voltage_l3` (whole volts) and the active power per phase as `power_l1`, `power_l2` and `power_l3` (whole watts). An idle wallbox reports none, so a wobbling mains voltage writes no history. |
| `sensor.*_total_energy` | Total energy | kWh | energy | layout v01.02 | Lifetime energy on the AC port. Usable as an Energy dashboard source; the manufacturer marks it as **not usable for billing**. |
| `sensor.*_session_energy` | Session energy | kWh | energy | always | Energy transferred in the current session. Attributes: `duration_minutes` (length of the session, in minutes, so the attribute changes once a minute rather than once a second), `maximum_current` (the highest current this session may use after every static limit, A) and `sessions` (lifetime number of charging sessions, layout v01.02). |
| `sensor.*_signaled_current` | Signaled current | A | current | layout v01.03 | What the wallbox offers the vehicle on the pilot line. This is the number that should follow your charging-current limit. |
| `sensor.*_charging_status` | Charging status | | enum | always | What the wallbox is doing; see [Charging status](#charging-status). |
| `sensor.*_charging_wait_reason` | Charging wait reason | | enum | always | Why the car does not charge yet; see [Charging wait reason](#charging-wait-reason). |

## Diagnostic sensors

These sit under *Diagnostic* on the device page. All except **Temperature**
are disabled by default.

| Entity | Name | Unit | Needs | Meaning |
|---|---|---|---|---|
| `sensor.*_temperature` | Temperature | °C | layout v01.02 | Temperature inside the wallbox. |
| `sensor.*_connection_quality` | Connection quality | | always | `good` or `degraded`, a stable summary of Modbus communication including heartbeat and write responses. It becomes degraded after a transaction that finally failed: a timeout or malformed response that no resend made good, or a heartbeat that missed its 10-second deadline. A lost reply recovered by a resend does not degrade it; a valid device error reply still proves communication. It returns to good after 300 seconds without a communication error and a new confirmed response. Attribute `heartbeat` is `ok` after a confirmed heartbeat and `problem` when no heartbeat is confirmed within ten seconds or an active sender stops; it is absent without a sender (read-only entries). Failure and retry counters are in the diagnostics download, so repeated retries do not create sensor history. |
| `sensor.*_authorization` | Authorization | | always | Whether RFID or the energy manager has released this session. The `enabled` attribute (layout v01.02) tells whether RFID authorization is switched on in the wallbox at all. |
| `sensor.*_cable_lock` | Cable lock | | always | Cable locking. A 4You 310 has a fixed cable and reports `fixed_cable`. The `permanent` attribute (layout v01.02) tells whether permanent cable locking is switched on. |
| `sensor.*_detected_vehicle_phases` | Detected vehicle phases | | layout v01.02 | How many phases the vehicle used in this session. The phases the wallbox itself will use are the `switched` attribute of **Requested phases**. |
| `sensor.*_wallbox_settings` | Wallbox settings | | always | The installation, read from the wallbox: the state is the register layout the firmware implements (`v01.00` … `v01.03`), which decides which entities exist. The attributes are what the installer set with DIP switches and the service tool, so they change only when someone opens the wallbox: `max_current_house` (the installation limit, DIP switches 6-8 on bank S2, A), `max_evse_current` (the wallbox's own maximum and the upper bound of the charging-current limit, A), `phase_rotation`, `grid_phases_connected` (layout v01.02), `phase_options` (whether the hardware can switch between one and three phases, layout v01.01), `phase_switching_mode` (how the internal solar algorithm may use phases), `phase_switching_pause_seconds` (how long the wallbox pauses when it switches phases, layout v01.02), `grid_imbalance_monitoring` and `grid_imbalance_threshold` (A, layout v01.02) and `solar_min_current` (the Sunshine+ minimum current, A, layout v01.02). The solar mode shown on the display is **Solar display mode**. |

## Binary sensors

| Entity | Name | Class | Needs | Meaning |
|---|---|---|---|---|
| `binary_sensor.*_vehicle_connected` | Vehicle connected | plug | always | EVSE and CP confirm a connected vehicle, including during faults. Conflicting or insufficient status is unknown. |
| `binary_sensor.*_charging` | Charging | battery_charging | always | The wallbox reports the charging state. |
| `binary_sensor.*_error` | Error | problem | always | An error code other than zero is active. The raw number is the `code` attribute, its documented category the `category` attribute. When the wallbox has fallen back because the energy manager's heartbeat stopped (layout v01.02), `heartbeat_lost` is `true` and `fallback` says what it does: keep the last values (`disabled`), pause (`pause_on_timeout`) or fall back to a fixed current (`fallback_current`, with the amperes in `fallback_current`). |
| `binary_sensor.*_guest_charging` | Guest charging | | always | On while a guest charge is active; see [Guest charging and shared time](#guest-charging-and-shared-time). |

## Activity

| Entity | Name | Needs | Meaning |
|---|---|---|---|
| `event.*_activity` | Activity | always | Commands the service carried out and changes the wallbox reported, as one event entity. |

The event type answers what kind of thing happened; the attributes say which
one. An automation triggers on the event type and narrows by attribute.

| Event type | When |
|---|---|
| `command_done` | A command was carried out (result `executed`), or error recovery was confirmed (`recovery_succeeded`). `executed` means the service completed the command, not that the vehicle has started charging; check **Charging** and actual power for that. |
| `command_failed` | A command failed (`failed`, including a phase-pause timeout), its write could not be confirmed (`uncertain`, inspect readback before retrying) or error recovery failed (`recovery_failed`). |
| `command_cancelled` | Queued work was cancelled (`cancelled`) or replaced by a newer request (`replaced`). |
| `session_started` | A car was plugged in. |
| `session_ended` | The car was unplugged. |
| `wallbox_changed` | Another change the wallbox reported; the `change` attribute names it. |

Command events carry these attributes:

| Attribute | Meaning |
|---|---|
| `action` | The command: `pause`, `resume`, `set_current`, `single_phase`, `all_phases`, `enable_charging`, `disable_charging`, `unlock`, `lock` or `recover`. |
| `result` | The outcome alone: `executed`, `replaced`, `cancelled`, `failed`, `uncertain`, `recovery_succeeded` or `recovery_failed`. |
| `reason` | Why: `completed`, `replaced`, `shutdown`, `phase_timeout`, `phase_unconfirmed`, `connection_error`, `write_rejected`, `permission_denied`, `status_refresh_failed`, `error_still_active`, `user_cancelled`, `energy_goal` (a charge target stopped it), `deadline_expired`, `time_limit` (an expired guest time prevents further starts), `guest_confirmation`, `vehicle_authorized`, `vehicle_waiting` or `unknown`. |
| `trigger` | Which function asked for it, not an authenticated person: `home_assistant`, `app`, `plug_charge`, `departure_plan`, `energy_goal`, `soc_goal`, `recovery`, `guest`, `unplug` or `unknown`. |
| `value` | The requested setting where relevant, not measured current; 0 for `set_current` means no software current limit. |
| `occurred_at` | When the service ran the command. The event's own timestamp is when HA received it. |

A queued request reports its result when it runs, is replaced or is
cancelled; **Charging wait reason** shows the wait. While you drag the current
slider, only the final value is reported, and a periodic automation that
repeats the same setpoint reports it once. Each command event also writes a
readable line to the logbook.

Session and wallbox events carry `change` and the values of that moment:

| `change` | Event type | Attributes |
|---|---|---|
| `vehicle_plugged_in` | `session_started` | `cp_state` |
| `vehicle_unplugged` | `session_ended` | `cp_state`, `session_energy` (kWh) and `session_duration` (s) last seen with the car |
| `charging_started` | `wallbox_changed` | `evse_state` |
| `charging_stopped` | `wallbox_changed` | `evse_state`, `session_energy`, `session_duration` |
| `error_occurred` | `wallbox_changed` | `error_code`, `error_category` |
| `error_cleared` | `wallbox_changed` | the code and category that were cleared |
| `authorized` / `authorization_rejected` | `wallbox_changed` | `authorization` |
| `downgrade_started` | `wallbox_changed` | `downgrade_current` (A) |
| `downgrade_ended` | `wallbox_changed` | |
| `cable_locked` / `cable_unlocked` | `wallbox_changed` | `cable_lock`; a fixed cable never reports this |
| `phases_switched` | `wallbox_changed` | `phases`: `all_available` or `single_phase` |

Attribute values are the same stable options as the matching sensors, so they
work in every UI language. A charge that ends with the unplug reports
`charging_stopped` first, then `vehicle_unplugged`.

Only a change between two known readings creates a wallbox event. The first
reading after HA starts, after a reload, after a lost connection or after a
failed poll is a baseline: nothing is reported for it, and a change that
happened in the gap is not reported at an invented time. A reading that is
briefly unknown, for example the vehicle presence during a wallbox fault,
keeps its last known baseline. A change that flips back within one poll
interval is not seen. Measurements, counters, configuration registers,
heartbeats and polling never create events.

This is a live notification feed, not a durable audit log. Startup, reconnect
and service restart establish a new baseline without replaying earlier
outcomes. See the [automation examples](automations.md#react-to-activity) and
[timeline details](troubleshooting.md#understand-the-activity-timeline).

## Enumerated states

What the enum entities can report. The option names are what automations
and templates see.

| Entity | States |
|---|---|
| `sensor.*_charging_status` | `initializing`, `wallbox_error`, `service_mode`, `no_vehicle`, `charging`, `locked`, `release_disabled`, `paused`, `authorization_required`, `preparing`, `waiting_for_vehicle`, `checking_status`, `state_uncertain`, `data_missing` |
| `sensor.*_charging_wait_reason` | `none`, `wallbox_unavailable`, `no_vehicle`, `release_off`, `plug_charge_waiting`, `meter_unavailable`, `vehicle_unconfirmed`, `until_planned_start`, `estimated_target_reached`, `waiting_for_device`, `waiting_for_soc`, `waiting_for_capacity`, `waiting_for_target`, `read_only`, `command_pending`, `target_reached`, `paused` |
| `sensor.*_connection_quality` | `good`, `degraded` |
| `sensor.*_authorization` | `authorized`, `not_authorized`, `not_used` |
| `sensor.*_detected_vehicle_phases` | `not_initialized`, `one_phase`, `three_phases`, `two_phases` |
| `sensor.*_cable_lock` | `fixed_cable`, `locked`, `undetermined`, `unlocked` |
| `select.*_requested_phases` | `all_available`, `single_phase` |
| `select.*_solar_display_mode` | `fast`, `not_active`, `sunshine`, `sunshine_plus` |
| `select.*_active_vehicle` | `none`, `automatic` (when automatic selection is available), and the names of your vehicle profiles |

### Attribute values

Enumerated attributes use the same option names; an undocumented register
value leaves the attribute out.

| Attribute | Values |
|---|---|
| `evse_state` on **Charging status** | `charging`, `error`, `ev_connected`, `idle`, `not_initialized`, `preconditions_valid`, `ready_to_charge`, `service_mode` |
| `cp_state` on **Charging status** (layout v01.02) | `a1`, `a2`, `b1`, `b2`, `c1`, `c2`, `d1`, `d2`, `e`, `f`, `init` |
| `category` on **Error** | `connected_phases_mismatch`, `energy_manager_unavailable`, `ev_overcurrent`, `no_error`, `other`, `voltage_out_of_range` |
| `fallback` on **Error** | `disabled`, `fallback_current`, `pause_on_timeout` |
| `switched` on **Requested phases** | `all_available`, `single_phase` |
| `heartbeat` on **Connection quality** | `ok`, `problem` |
| `phase_rotation` on **Wallbox settings** | `l1_l2_l3`, `l2_l3_l1`, `l3_l1_l2` |
| `grid_phases_connected` on **Wallbox settings** | `l1`, `l1_l2_l3` |
| `phase_options` on **Wallbox settings** | `one_phase_only`, `one_or_three_phases`, `three_phases_only` |
| `phase_switching_mode` on **Wallbox settings** | `solar_dynamic`, `solar_one_phase`, `solar_three_phases` |

## Actions

| Action | Meaning |
|---|---|
| `mennekes_amtron_rtu.set_charging_current` | Writes the charging-current limit. Like the slider, it accepts 0 A to remove the limit, with `allow_unlimited`. See [user-guide.md](user-guide.md#actions). |
| `mennekes_amtron_rtu.start_guest_charging` | Same as the **Start guest charging** button. |
| `mennekes_amtron_rtu.clear_target_time` | Clears **Target time** without changing energy or vehicle targets. |
| `mennekes_amtron_rtu.use_calendar_departure` | Copies one matching timed event from the next 14 days of the configured calendar into **Target time**. Later events are not selected automatically. Needs a calendar and an exact event title. |
| `mennekes_amtron_rtu.use_vehicle_departure` | Copies one upcoming active climate timer from locally published vehicle states into **Target time**. Implemented for BavarianData; recurring timers need explicitly configured weekdays. No cloud fetch. |
| `mennekes_amtron_rtu.assign_vehicle_charge` | Pairs the current vehicle kWh value with the latest completed wallbox charge while unplugged, for the [loss estimate](charging-estimates.md). Also works read-only. |
| `mennekes_amtron_rtu.get_recorder_exclusions` | Returns a Recorder exclusion suggestion with your actual entity IDs; see [Recorder](recorder.md). |

The device actions take a `device_id`, which can be left out when only one
wallbox is set up.

## Optional charging estimates

| Entity | Name | Unit | Needs | Meaning |
|---|---|---|---|---|
| `sensor.*_estimated_charging_loss_last_charge` | Estimated charging loss, last charge | % | vehicle profile with an energy source | Last matched charge; unknown when the newest completed charge cannot be matched. The `association` attribute says how it was matched (`history`, `changed_value` or `manual`). |
| `sensor.*_average_estimated_charging_loss` | Average estimated charging loss | % | vehicle profile with an energy source | Energy-weighted average of accepted matches; `sample_count` counts accepted charges. |
| `sensor.*_estimated_charging_cost_current_charge` | Estimated charging cost, current charge | EUR | fixed or dynamic price configured; enable the entity | Disabled by default to reduce Recorder writes. Running variable cost estimate; unknown for incomplete observations. |
| `sensor.*_estimated_charging_cost_last_charge` | Estimated charging cost, last charge | EUR | fixed or dynamic price configured | Cost of the latest completed charge, retained across restarts. |

All estimates require layout v01.02 or newer. They have no state class and
create no long-term statistics. Losses update per match; costs are rounded to
cents. The current-cost sensor exposes only `complete`; the last-cost sensor
also exposes `energy_kwh` and `unpriced_kwh`, rounded to 0.01 kWh. Setup,
units, §14a tariff handling, matching limits and Recorder behavior are
described in [Charging losses and costs](charging-estimates.md).
