# Everyday charging

[Guide home](README.md) · [Dashboard YAML](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Open your wallbox under **Settings → Devices & services → Mennekes Amtron RTU**,
or use the [dashboard example](app-from-home-assistant.md). Names follow your HA language.

## Choose what you want to do

| Your intention | What to do |
|---|---|
| Charge normally | Set the current, allow charging release and turn **Charging paused** off. |
| Stop for now | Turn **Charging paused** on. Check **Pending change** if it cannot run immediately. |
| Add a limited amount of energy | Set **Energy target**, for example 12 kWh. Start or resume separately. |
| Charge a visitor's car | Optionally set kWh and a time, then press **Start guest charging**. |
| Stop a selected car at a percentage | Configure its battery source, then set **Vehicle charge target**. |
| Have a recognized car ready for a trip | Enable its optional [departure plan](departure-planning.md), set percentage and target time. |

There is no charging order to create, save, clear or schedule. Limits are ordinary
controls shared by HA and the app. Saving a limit does not start charging.

## Shared limits and target time

| Setting | Guest charging | Recognized vehicle |
|---|---|---|
| **Energy target** | Stops after the saved additional wallbox kWh. | Same energy ceiling. |
| **Target time** | Stops at that time; it does not delay the start. | With planning enabled, the next trip's departure time. |
| **Vehicle charge target** | Ignored; its saved value is retained. | Battery percentage ceiling from the vehicle's reported SoC. |

For a guest, **the first reached kWh or time limit stops charging**. With neither
set, there is no hidden limit. Enter **0** to disable a kWh or percentage target.
Clear the target time separately; the Mushroom card offers this by holding the time
tile. Changing or clearing a limit does not itself start guest charging.

A positive kWh setting starts a new meter baseline when you save it. Guest start
uses that baseline; it does not add the amount a second time. An already reached
energy goal must be changed or disabled before charging can resume.

A vehicle departure plan can lift a manual pause. Clear its time before pausing
if you want the recognized vehicle to remain stopped. It never takes control of
the current guest connection, including after you manually pause that guest.

## Guest charging

Requires the [service app](modbus-service.md) with writes enabled.

1. Plug in the visitor's car.
2. Optionally set **Energy target** and/or a future **Target time**. In the app,
   save each changed field first.
3. Press **Start guest charging**. Charging is requested immediately: a manual
   pause is lifted and virtual Plug & Charge permission is granted for this plug-in.
4. Use the ordinary **Charging paused** switch to stop early. The usual current
   slider remains available throughout.

Locks, wallbox faults, local authorization, write intervals and electrical limits
still apply. “Started” means the request was accepted; check actual power and any
pending change. The vehicle's percentage target and intelligent plan do not
control a guest connection. Unplugging ends guest permission.

There is no separate guest record, energy baseline or forecast. Permission belongs
to the running service and this physical connection. An HA Core restart leaves
it running; an app/service restart loses that guest designation. After restarting
the app, check the limits and explicitly start guest charging again before relying
on its time cutoff. The shared energy target and ordinary wallbox intent are retained.

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

A queued request is not a completed change. **Pending change** shows the action
and its `due` time when known. The latest request replaces an earlier one;
repeating the same request does not extend its wait.

| Guard | Default | Meaning |
|---|---|---|
| State changes | 300 seconds; configurable 0–3600 | Starts, pauses, resumes, phases, release and lock changes can wait. |
| Current writes | 5 seconds; fixed | Protects the register even if the longer wait is disabled. |
| Phase grace | 5 seconds; configurable 0–30 | Extra wait after the car confirms a pause. |

Set waits under **Configure → Wallbox** for direct mode or **App → Settings →
Runtime** for service mode. Confirmed idle readings with no vehicle skip the
longer wait. Unknown readings do not. MENNEKES recommends more than five minutes;
choose, for example, 360 seconds to exceed that recommendation.

Even a manual pause, lock or release-off can wait. Check actual power; this is
not an emergency-stop control. Goal stops have their own rules below. An owner
restart cancels its queue; an HA restart does not restart the separate service.

## Cancel a queued change

Press **Cancel pending change** while a request is waiting. It removes queued
work without undoing commands already sent. A phase-change pause already applied
stays paused. Service mode needs the app reachable and write-enabled, but the
wallbox itself may be offline.

## Configuration and options

Use **Configure** for optional features and **Reconfigure** to change the
integration's connection. Keep the existing entry to retain device/entity IDs.

| Setting | Direct USB/LAN | Service app |
|---|---|---|
| Adapter, protocol, host, address | Entry **Reconfigure** | **Settings → Connection**, save and restart app |
| Current cap, polling, waits | **Configure → Wallbox** | **Settings → Runtime**, preview and confirm; no restart |
| Write permission | **Configure → Wallbox → Current limit and polling → Control mode** | Native **Configuration → Read-only**, save and restart app |
| Vehicle sources and planning | **Configure → Vehicles** | Same HA menu |
| Fixed/dynamic prices and network tariffs | **Configure → Charging costs** | Same HA menu |

Only relevant follow-up forms appear. The vehicle wizard saves on **Save and
finish** or its final area; other sections save on their last input form. Cancel
the unfinished section to discard its edits. The app is the only software current
cap in service mode. Native app Configuration changes need an app restart.
[Connection and update guide](installation.md).

## Energy target

A positive value, 0.1–1000 kWh, means **additional wallbox energy from the moment
you save it**. Saving the same positive value again establishes a new baseline.
It does not start charging. At the target, the controller requests pause and
cancels pending charging changes. The five-second write guard remains, but this
stop bypasses the longer state-change wait. Check actual power to confirm the stop.

Meter resolution, polling and communication delays can cause an overshoot; this
is not an exact energy cutoff. The lifetime counter is required (Modbus layout
v01.02 or newer). The goal and its baseline survive an owner restart.

| Status | What to do |
|---|---|
| `active` | Monitoring the saved baseline. |
| `reached` | Change the goal or set 0 before resuming; neither action itself resumes charging. |
| `meter_unavailable` | The goal pauses rather than guessing. Valid readings restore monitoring. |
| `meter_reset` | Check the counter, then set a new goal or disable it with 0. |

A meter-outage pause can resume automatically only if the goal caused it, the
owner is still running, no other stop applies and you have not issued a manual
control command meanwhile. After restarting the owner, resume explicitly.
Heartbeat recovery also respects deliberate pauses and reached goals.

Read-only mode cannot enforce a goal. Direct mode requires HA running; the service
can enforce it during HA Core downtime. Moving between owners does not transfer
an active goal: set it again with the new owner. A counter reset during downtime
that ends above the saved baseline cannot be reliably distinguished.

## Phase switching

**Requested phases** appears only on supported hardware. Check vehicle support
before using it. During charging, switching first pauses and does not resume
automatically. Wait for the phase command to finish, then resume explicitly.

The owner waits up to 30 seconds for fresh confirmation that the car paused,
then adds the configured phase grace and checks again. If confirmation fails,
it sends no phase change and retains the pause. The separate state-change
and current-write guards still apply. Confirmed idle with no car skips the
vehicle wait. [Troubleshooting](troubleshooting.md#phase-switching-leaves-charging-paused).

## Heartbeat and Home Assistant restarts

| What restarts or fails? | Effect |
|---|---|
| HA Core, while the separate app keeps running | Heartbeat, applied state, shared energy goal and accepted app planning continue. HA automations and new vehicle reports stop temporarily. |
| HA Core in direct mode | Heartbeat is interrupted; wallbox fallback applies. Error 200 recovery runs when communication returns and respects deliberate pause, release, lock and goals. |
| App/service restart | Heartbeat is interrupted and queued commands are cancelled. Ordinary intent and limits are retained; temporary guest permission is lost. [Check and restart the guest](#guest-charging). |
| Host, power or adapter failure | The owner may lose communication; the configured wallbox fallback applies. |

HA reconnects to the service's current state without replaying old charging
commands. A managed heartbeat or service-connection fault lasting 60 seconds
raises a repair when writes are enabled; recovery clears it automatically.
A lost HA-to-service link alone does not prove the heartbeat stopped.
[Heartbeat help](troubleshooting.md#error-200-energy-manager-unavailable) ·
[Fallback](safety.md#the-heartbeat-and-what-happens-without-it).

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
