# Using your wallbox

Open **Settings → Devices & services → Mennekes Amtron RTU** to find your
wallbox. This guide uses the English labels; the integration and app also
include German, French, Dutch and Italian translations.
New here? Follow the [Quick start](quickstart.md) first.

Start with the current slider and pause switch. Add vehicle data, charging
targets and schedules only when you want them:

- [First charge](quickstart.md#7-first-charge)
- [Choose optional functions](features.md)
- [Vehicle setup and selection](vehicle-setup.md)
- [Charge for the next departure](departure-planning.md)
- [Losses and costs](charging-estimates.md)

Vehicle profiles, optional automatic selection, Virtual Plug & Charge and app
live debugging are available in **beta 0.8.0b1**. **0.6.1 remains stable**; see
[version availability](README.md#versions-and-feature-availability).

## Optional charging losses and costs

Use an existing vehicle-energy source and a fixed or dynamic electricity price.
Losses and costs are independent and off by default. The running cost sensor is
also optional, so you can keep only a completed-charge result in history.
[Choose the right data and set up estimates](charging-estimates.md).

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

Current writes have a **five-second minimum interval**. During charging, a new
current request may wait until that interval ends; the latest request wins.
A separate configurable wait applies before starting or changing the charging
state, described below.

## Waiting between charging changes

A requested change may be queued. **Pending change** tells you what will happen;
**Pending change due** shows when, once the timing is known. You do not need to
press the control again. The most recent charging request replaces the previous
queued request; repeating the same request does not extend its wait.

| Setting | Default | Where to change it |
|---|---|---|
| Minimum time between state changes | **300 seconds**, configurable from 0–3600 | Direct: integration options. Service: app configuration, then restart the app. |
| Minimum interval for current-register writes | **5 seconds** | Required write guard; remains active when the longer wait is set to 0. |
| Extra wait before a phase change | **5 seconds**, configurable from 0–30 | Same connection owner; see [phase switching](#phase-switching). |

The longer interval applies to starts, pauses, resumes, phase changes, charging
release and wallbox lock changes. **Even pause, disable-release and lock requests
can wait**, so charging may continue until the shown time. Changing the current
while paused can queue a resume. Goal-triggered stops use their own rules below.

With a confirmed idle wallbox and no vehicle, manual changes skip the longer
wait. Unknown or inconsistent wallbox readings retain it. The heartbeat and
five-second write guards continue in either case. A queued phase change first
needs confirmation that the vehicle has paused, so its due time can temporarily
be unknown.

MENNEKES recommends **more than five minutes** between charging transitions.
The default is 300 seconds; choose **360 seconds**, for example, to exceed that
recommendation. PV automation still needs suitable start/stop thresholds and
hold times; the delay alone cannot assess available surplus.

Use [Cancel pending change](#cancel-a-queued-change) to remove a queued request.
An owner restart also cancels it: HA reload/restart in direct mode, or app/service
restart in service mode. Restarting HA Core alone leaves the separate service's
queue running. After a cold start, a fresh guard period protects transitions
whose previous timing is unknown.

## Where do I enable controls?

The setting depends on how you connect the wallbox:

| Connection | Where to enable writes | What you see in HA |
|---|---|---|
| **Direct USB or LAN** — the integration connects to the adapter | Integration entry **options → Control mode → Modbus master**. New entries start **Read only**. | Controls are added in master mode. |
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

Open **Settings → Devices & services → Mennekes Amtron RTU → Configure**.
Choose one of five independent sections:

| Section | What you configure |
|---|---|
| **Wallbox** | Control mode, polling, current cap and transition timing, as applicable to the connection |
| **Vehicle** | Vehicle sources, optional profiles/selection, Virtual Plug & Charge and the percentage target and charge limit |
| **Next departure** | Optional planning for the next trip, battery capacity and the departure sources you want |
| **Charging costs** | A fixed price or price entity; German network tariffs when applicable |
| **Charging losses** | A last-charge energy entity or a supported vehicle history adapter |

Only the fields for your selected choices appear. Confirm the last input
form to save that section directly; there is no extra confirmation page.
Cancel or **Back** discards an unfinished section. Other sections keep their
settings, and a concurrent edit to the same section is reported instead of
being overwritten.

Use **Vehicle** to configure a vehicle profile once, then choose which optional
features should use it. BavarianData is the first supported vehicle integration.
You can also select existing HA entities manually, including entities from other
vehicle integrations. Calendar and manual departure inputs remain available.

| Setting | Direct USB or LAN connection | Modbus service connection |
|---|---|---|
| Write permission | **Configure → Wallbox → Current limit and polling → Control mode** | App configuration: **Read-only** |
| USB adapter, or LAN protocol/host/TCP port, and wallbox address | Integration entry: **Reconfigure** | App: **Open Web UI** |
| Baud rate and frame | USB: **Reconfigure**; LAN: adapter interface or optional [adapter setup](lan-adapters.md) | USB: app **Open Web UI**; LAN: adapter interface or optional [adapter setup](lan-adapters.md#service-app) |
| Status and measurement polling | Integration options | App configuration |
| How often HA fetches service status | Not applicable | Integration options: **Status polling interval** |
| Additional current cap for HA commands | Integration options: **Charging current cap** | Same HA option; the service's current cap also applies |
| Minimum time between state changes | Integration options, in seconds | App configuration or standalone service configuration, in seconds |
| Extra wait before phase changes | Integration options, in seconds | App configuration or standalone service configuration, in seconds |

The app keeps connection settings under **Open Web UI** and operating settings
under **Configuration**. Save and restart the app after changing those settings.
If a published app refuses to open the guided page on Supervisor 2026.10.x,
use its native Configuration tab; see [the connection-page version note](upgrading.md#app-connection-page).

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

With a **direct USB or LAN connection**, a Core restart or entry reload interrupts
the heartbeat. Once communication returns, error 200 recovery runs
automatically. It preserves a deliberate pause, lock, disabled charging release
and current setting. If recovery cannot complete, follow
[heartbeat troubleshooting](troubleshooting.md#error-200-energy-manager-unavailable).

The service cannot bridge its own shutdown, a host restart or a lost USB/LAN
connection. In those cases, the wallbox follows its configured
[fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).
Check that behavior before relying on unattended charging.

A continuous heartbeat or service-connection fault raises a repair after
**60 seconds** when the heartbeat is managed and writes are enabled. It clears
automatically after recovery; a connection repair has no recovery button.
A lost HA-to-service link does not establish that the app stopped sending its
heartbeat. See [connection repairs](troubleshooting.md#heartbeat-or-service-connection-repair).

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

**Solar display mode** changes the mode shown on the wallbox; it does not
measure PV surplus or run a surplus-charging algorithm. It requires the hardware
configuration described in the [hardware guide](hardware.md).

Wallbox current limits do not account for other household loads. A simple PV
automation is not dynamic protection for the whole house connection.

## Phase switching

**Requested phases** appears only on hardware that supports switching. Check
your vehicle's support before using it. Switching during charging pauses first
and **does not resume automatically**. Resume explicitly after the waiting
interval and check the result; some vehicles abort the session.

Phase changes share
the [configurable state-change interval](#waiting-between-charging-changes)
and an early request is queued. Wait until the phase request has actually run
before requesting resume; a new resume request would replace a queued phase
request. Before changing phases, the integration waits up to **30 seconds** for
live confirmation that the vehicle has paused. It then waits an additional
**5 seconds** by default and checks the state again before sending the phase
command. Configure **Extra wait before phase changes** from **0 to 30 seconds** in
the integration options for direct connections, or in the app/service for
service connections. Zero removes only the extra grace time, not pause
confirmation or the separate 300-second state-change interval. If confirmation
fails or times out, the phase command is not sent and the requested pause
remains in place. With no vehicle connected and the wallbox idle, there is no
vehicle pause to confirm or extra wait. Fresh Idle and CP A1/A2 readings also
skip the longer state-change interval; register-write guards still apply.
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

## Energy target

Set **Energy target** to the additional energy you want the wallbox to supply,
for example **12 kWh**. The range is 0.1–1000 kWh in 0.1 kWh steps. Each positive
setting establishes a new goal from a fresh lifetime-energy reading, even if
you enter the same number again. It **only limits** a charge: it does not start
charging, lift a pause, unlock the wallbox or enable charging release. Start
charging yourself or through your existing automation.

At the target, the owner requests a pause and cancels pending charging changes.
The five-second current-write interval remains, but this stop does not wait for
the longer state-change interval. **Energy target status** changes to `reached`;
check **Charging paused** and actual power to confirm that charging stopped.
The goal is not an exact energy cutoff: measurement cadence (normally up to
60 seconds for the lifetime counter), meter resolution, communication delays
and the remaining write interval can cause additional energy to be delivered.

The target and meter baseline survive a controller restart. The independent
service continues enforcing them during HA Core downtime. Direct mode can only
monitor and pause while HA is running. A failed or unreadable meter pauses the
charge instead of guessing how much energy was delivered. A falling counter
requires your attention. After restart, a counter below the saved baseline
is also detected; a counter reset during downtime that ends above the baseline
cannot be distinguished reliably without saving every observation.

With `meter_unavailable`, fresh valid meter readings automatically restore
monitoring against the original target. If the goal itself paused a previously
unpaused charge, it can resume through the normal waiting interval. A deliberate
pause, another charging/current/phase/lock/release command, or a new goal cancels
that automatic restart. An already
reached goal, a reset counter, or another active goal's stop remains paused.
Heartbeat-error recovery likewise respects active goals and deliberate pauses;
this does not introduce an automatic reset of unrelated wallbox faults.
Permission to restart after a meter outage lasts only for the running owner.
After restarting that owner, the target is retained but its error pause stays
paused until you resume it. Restarting HA alone does not restart the separate app.

For `meter_reset`, check the meter and set a new goal or enter **0** to disable it.
Neither setting nor disabling a goal resumes charging. A reached goal blocks
start/resume until you change or disable it. Other limits, authorization, deliberate pauses,
locks and release settings still apply. Read-only mode disables all writes,
including goal enforcement.

The goal requires the lifetime energy counter (Modbus layout v01.02 or later).
An older compatible service keeps its existing functions; update the service to
use the new goal controls. Switching an entry between direct and service mode
does not transfer an active goal: the new connection owner has its own stored
state. Set the intended goal with that owner before continuing the charge.

## Vehicle profiles

**New in beta 0.8.0b1.** Keep each car's sources, optional features and
loss averages together. Select the connected car manually, or opt into the
separate automatic-selection feature. A profile does not enable charging
features by itself. Switching cars clears the former percentage target and trip
without starting a charge.

[Set up one car or several profiles](vehicle-setup.md#vehicle-profiles).

### Optional automatic vehicle selection

**New in beta 0.8.0b1 and off by default.** Use the selected vehicle integration or
explicit cable/home entities. Exactly one sufficiently clear candidate can be
selected; uncertain matches leave no active vehicle. Choosing a car manually
turns the automatic mode off. These signals are a selection aid, not vehicle
identity supplied by the wallbox.

[Configure automatic selection and understand its limits](vehicle-setup.md#optional-automatic-vehicle-selection).

### Optional Virtual Plug & Charge

**New in beta 0.8.0b1 and off by default.** Separately enable this feature and permit the
profiles that may receive automatic charging release. It needs an unambiguous
cable/home match for the active car and a fresh cable report for the current
connection cycle. Manual profile selection alone does not grant permission.
Existing pauses, locks, disabled release and reached targets remain effective.
The status sensor explains what it is waiting for.

Enabling it may stop a charge while recognition is pending. Disabling it does
not resume charging; use **Charging release** explicitly afterwards.
[Set up Virtual Plug & Charge](vehicle-setup.md#optional-virtual-plug--charge).

## Optional vehicle percentage target

Choose the vehicle's charge-level source, then set **Vehicle charge target**, for
example 80%. The car's reported maximum limit is a separate optional choice.
Setting a target only limits charging; it does not lift a pause. **0%** disables
the target without resuming. Missing SoC data leaves charging running, so keep
the vehicle's own limit configured when that is important to you.

[Percentage-target setup, limits and missing data](vehicle-setup.md#optional-vehicle-percentage-target).

## Charge for the next departure

Choose one full date and time, a positive percentage target and usable battery
capacity. The plan adjusts current and can pause or resume charging toward that
time. **It can also lift a manually set pause**; clear **Next departure** to
stop the plan. Manual, calendar and vehicle-timer inputs can coexist.

[Set up a trip, import a calendar or BMW climate time, and add preconditioning](departure-planning.md).

## Cancel a queued change

While **Pending change** shows an action, press **Cancel pending change**.
This removes the queue entry and does not send a wallbox command. A phase-change
pause already performed stays paused. The remembered current setpoint is kept;
cancellation does not undo a command that has already reached the wallbox.

Direct-mode cancellation also works during a failed wallbox poll. Service mode
requires the app to be reachable, authenticated and write-enabled, but its
wallbox connection may be down. Older services need updating to support this
button. It becomes unavailable again when there is no pending work.
