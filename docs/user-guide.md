# Everyday charging

[User guide](README.md) · [Dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Open **Settings → Devices & services → Mennekes Amtron RTU** and select the
wallbox. The examples use English control names; HA translates them into your
selected language.

**[Dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards)** ·
**[Choose optional features](features.md)** · **[Help](troubleshooting.md)**

[Controls](#everyday-controls) · [Enable writes](#where-do-i-enable-controls) · [Waiting](#waiting-between-charging-changes) · [Settings](#configuration-and-options) · [kWh target](#energy-target)

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
| **Charging** / **Charging status** | The charging state reported by the wallbox, and why it is not charging. The raw wallbox states are the `evse_state` and `cp_state` attributes. |
| **Signaled current** | The current offered to the vehicle. Hardware limits or a grid-operator downgrade can reduce it. The vehicle may draw less. |
| **Total power** | The actual combined charging power. |
| **Session energy** | Energy delivered during this session. |
| **Total energy** | The lifetime counter for the Energy dashboard. It is not usable for billing. |
| **Downgrade active** | A hardware restriction from the grid operator, with the applied limit as the `current` attribute. An automation cannot override it. |
| **Connection quality** | Bus health, with heartbeat delivery as the `heartbeat` attribute (`ok` or `problem`). It records status changes rather than every heartbeat. |

The [entity reference](entities.md) lists all entities, supported features and
the exact state values available to templates.

## Waiting between charging changes

A requested change may be queued. **Pending change** tells you what will happen;
its `due` attribute shows when, once the timing is known. You do not need to
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

## Cancel a queued change

While **Pending change** shows an action, press **Cancel pending change**.
This removes the queue entry and does not send a wallbox command. A phase-change
pause already performed stays paused. The remembered current setpoint is kept;
cancellation does not undo a command that has already reached the wallbox.

Direct-mode cancellation also works during a failed wallbox poll. Service mode
requires the app to be reachable, authenticated and write-enabled, but its
wallbox connection may be down. It becomes unavailable again when there is no pending work.

## Configuration and options

Open **Settings → Devices & services → Mennekes Amtron RTU → Configure**.
The main menu has three sections:

| Section | What belongs here |
|---|---|
| **Wallbox** | Direct mode: current cap, polling and charging waits. App mode: displays the app's values. |
| **Vehicles** | Each car's sources, percentage limit, departure planning, losses and detection signals. |
| **Charging costs** | Fixed or dynamic energy price and optional German network tariffs. |

The vehicle wizard saves at **Save and finish** or its final area. Other
sections save on their last input form. Cancel an unfinished section to discard
its draft. Features appear only when their prerequisites are configured.

| Change | Direct USB/LAN | With the service app |
|---|---|---|
| Adapter, protocol, host or address | Entry **Reconfigure** | **Open Web UI → Settings → Connection**, save and restart app |
| Current cap, polling and charging waits | **Configure → Wallbox** | **Settings → Runtime**, preview and confirm; no restart |
| Permission to write | **Configure → Wallbox → Current limit and polling → Control mode** | Native **Configuration → Read-only**, save and restart app |
| Vehicle data and costs | **Configure** | **Configure** in HA |

The app's current cap is the only software cap in service mode. HA does not
add another. **0 A** removes the EMS current limit, including that cap;
hardware limits still apply. The app's native Configuration tab also contains
runtime settings, but changes made there need an app restart.

Keep the entry when changing its connection; matching the wallbox's serial
number preserves device and entity IDs. [Move to the app](modbus-service.md#move-an-existing-wallbox).

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
Switching an entry between direct and service mode
does not transfer an active goal: the new connection owner has its own stored
state. Set the intended goal with that owner before continuing the charge.

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

## Heartbeat and Home Assistant restarts

The **optional Modbus service is recommended** if you want charging to continue
through Home Assistant Core restarts. It keeps the heartbeat running and holds
the last charging state while HA is offline, including an active pause. HA
reconnects to that state without replaying old charging commands. HA automations
are still offline during a Core restart.

For a charge that must run while HA is offline, the app can hold one
[guest session](app-guide.md#guest-charging) itself, using the shared optional
kWh and time limits for the plugged-in car. While such an order runs, HA
cannot change the current or resume; pausing in HA stops the order. When it
ends, charging stays paused and HA controls charging again.

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

## Vehicle profiles

[Set up a vehicle](vehicle-setup.md) only if you want vehicle data. The current
slider, pause switch and kWh target work without it.

### Optional automatic vehicle selection

Configure cable and home signals for each car, then select **Automatic
selection** in **Active vehicle**. [Setup and matching rules](vehicle-setup.md#optional-automatic-vehicle-selection).

### Optional Virtual Plug & Charge

This separate switch controls charging permission. A fresh recognized plug-in
can lift a previous manual pause; a grant restored after a restart does not.
Locks, reached targets and an active departure plan still matter.
[Set up permission and understand its limits](vehicle-setup.md#optional-virtual-plug--charge).

## Optional vehicle percentage target

Set **Vehicle charge target** to the wanted percentage. **0%** disables only
that target; neither action starts charging. Missing SoC does not pause a charge.
[Configure the source and optional vehicle maximum](vehicle-setup.md#optional-vehicle-percentage-target).

## Charge for the next departure

Choose one dated trip, usable battery capacity and a percentage target.
**The plan may lift a manual pause.** Clear **Next departure** to cancel it.
[Manual, calendar and vehicle-timer setup](departure-planning.md).

## PV charging and schedules

Use the [solar and calendar blueprints](blueprints.md). **Solar display mode**
only changes the wallbox display; it is not a surplus controller. Household
load protection remains the responsibility of the installation.

## Actions

Use [automation examples](automations.md) for current control and events, or
[app actions](app-from-home-assistant.md#automation-actions) for guest charging. [Recorder settings](recorder.md) explains the exclusion-list action.
