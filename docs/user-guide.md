# Using your wallbox

Open **Settings → Devices & services → Mennekes Amtron RTU** to find your
wallbox. This guide uses the English labels; the integration and app also
include German, French, Dutch and Italian translations.
New here? Follow the [Quick start](quickstart.md) first.

## Optional charging losses and costs

Choose a vehicle-energy source and a fixed or dynamic electricity price in the
integration options. Both are independently optional and disabled by default. Tibber and other existing
HA price sensors can be selected; §14a network components are configured
separately. The optional current-cost entity must also be enabled explicitly;
the last-cost sensor is sufficient if you only want completed charges in history.
See [Charging losses and costs](charging-estimates.md) for setup,
data-quality choices and restart behavior.

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

**Minimum time between state changes** defaults to **300 seconds**. Enter a
whole number from **0 to 3600 seconds**. With a direct USB or LAN connection,
change it in the integration options. With the Modbus service, change it in
the app configuration or standalone `state_change_interval_seconds` setting,
then restart the service. HA does not apply a second interval on top of it.

An early pause, resume, phase change, charging-release change or wallbox lock
change is **queued** and runs when the applicable interval has elapsed.
Changing the current while paused can also queue a resume. The latest charging
request replaces an older pending request. Repeating the same requested state
does not extend the wait.

With **no vehicle connected**, the longer wait is skipped when fresh wallbox
readings agree: EVSE is Idle and CP is A1 or A2. Unplugging during a queued
wait is checked every five seconds, so the request can run earlier than the
displayed due time. The five-second register-write guards and heartbeat remain.
Unknown, inconsistent or unreadable status keeps the longer wait; older register
layouts without CP status also keep it.

**Pending change** shows what is waiting, and **Pending change due** shows
the planned execution time when known. While a phase change waits for the
vehicle to confirm its pause, the action stays visible but its due time is
unknown. Once confirmed, the due time shows the end of the extra wait. Home
Assistant can display a known timestamp relative to the current time. That
timestamp stays fixed during a timed wait, so a countdown does not create a
database update every second. Pause, release, lock and phase
controls continue to show the wallbox's actual state. While paused, the current
slider shows the remembered resume target, which can already reflect the queued
request; it does not indicate that charging has started.

Pausing, disabling charging release or locking the wallbox replaces a pending
start with that new request. **These commands also wait for the interval:**
charging may continue until the displayed due time. A pause within five seconds
of the previous current write is also queued, so it does not need to be sent
again. Error recovery can reassert the existing state and the heartbeat
continues throughout the wait.
Setting the interval to **0** disables the longer wait, while the technical
write guards remain.

MENNEKES recommends **more than five minutes** between charging transitions.
The configurable default is 300 seconds; choose **360 seconds**, for example,
to exceed that recommendation's five-minute threshold. Keep separate PV start
and stop thresholds and hold times too: the interval alone does not decide
whether enough surplus is available.

A pending command is cancelled if its owner restarts: an integration reload or
HA Core restart in direct mode, or an app/service restart in service mode.
An HA Core restart alone leaves a running service's pending command active;
it can therefore execute while HA is offline. After a cold start, a fresh
guard period protects transitions whose previous timing is unknown.

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

| Setting | Direct USB or LAN connection | Modbus service connection |
|---|---|---|
| Write permission | Integration options: **Control mode** | App configuration: **Read-only** |
| USB adapter, or LAN protocol/host/TCP port, and wallbox address | Integration entry: **Reconfigure** | App: **Open Web UI** |
| Baud rate and frame | USB: **Reconfigure**; LAN: gateway configuration | USB: app **Open Web UI**; LAN: gateway configuration |
| Status and measurement polling | Integration options | App configuration |
| How often HA fetches service status | Not applicable | Integration options: **Status polling interval** |
| Additional current cap for HA commands | Integration options: **Charging current cap** | Same HA option; the service's current cap also applies |
| Minimum time between state changes | Integration options, in seconds | App configuration or standalone service configuration, in seconds |
| Extra wait before phase changes | Integration options, in seconds | App configuration or standalone service configuration, in seconds |

App **0.6.0** moves USB/LAN connection selection to
**Open Web UI**, with an adapter dropdown and fields for the chosen mode.
Write permission and other operating settings stay in **Configuration**.
Save and restart the app to apply changes; see [the connection page](upgrading.md#app-connection-page)
for its Supervisor requirement. Older app **0.5.2b2** uses the native configuration
for all app settings.

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

**Solar display mode** (called **Solar charging mode** in 0.5.2b2) changes the mode shown on the wallbox; it does not
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

## Optional vehicle percentage target

The percentage target is **off by default**. In the integration's options,
select **Vehicle charge level entity** from your existing vehicle integration.
This adds **Vehicle charge target** and its status sensor. The integration only
reads that vehicle entity; it does not change the car's settings or estimate
battery capacity from wallbox energy.

**Vehicle charge limit entity** is a separate, optional selection. Without it,
you can choose a target up to **100%**. With it, the reported car limit becomes
the maximum you can select. A falling car limit also lowers an existing target;
a later increase never raises the target you chose. If this optional source is
temporarily unavailable, the last valid maximum is kept. Before its first valid
reading, setting a positive target is refused rather than assuming 100%.

For example, with a reported charge level of 45%, set a target of **80%**.
Charging pauses when the vehicle reports at least 80%. Setting a target never
starts charging or lifts a pause; **0%** switches this target off without
resuming. A reached target stays reached even if the reported charge level later
falls. Setting a new positive target establishes a new limit. The independent
kWh target can also remain active: reaching either target stops the charge.

As selected for this integration, missing or invalid vehicle data **does not
pause charging**. Monitoring resumes when a valid value returns. The same applies
while HA is down: the service can keep charging, but cannot evaluate an external
percentage until HA sends vehicle data again. Delayed or unchanged reports from
the vehicle integration can therefore allow the target to be exceeded. This is
not a replacement for the car's own charge limit. The five-second write floor
and wallbox response time also still apply when a stop is requested.

Changing or removing either selected source clears the old percentage target
and remembered maximum without starting charging. Clear the SoC selection to
turn off the whole percentage feature; also clear the maximum selection if one
was configured. The kWh goal is independent. Service
mode needs an app version advertising percentage-goal support; existing service
functions remain available on older compatible versions.

## Cancel a queued change

While **Pending change** shows an action, press **Cancel pending change**.
This removes the queue entry and does not send a wallbox command. A phase-change
pause already performed stays paused. The remembered current setpoint is kept;
cancellation does not undo a command that has already reached the wallbox.

Direct-mode cancellation also works during a failed wallbox poll. Service mode
requires the app to be reachable, authenticated and write-enabled, but its
wallbox connection may be down. Older services need updating to support this
button. It becomes unavailable again when there is no pending work.
