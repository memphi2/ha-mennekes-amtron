# Updating an existing installation

**Keep your existing wallbox entry.** Routine updates
do not require deleting devices, entities or automations. Moving from HACS to
the Marketplace also keeps the same integration and entry.

**Current stable release: 0.8.0.** In service mode, update the app/service first,
then the integration. Keep existing app data and your HA entry. The **#apps**
catalog carries stable updates and also reaches existing **#apps-beta**
installations. Check the [app connection choice](#app-connection-choice)
before updating from 0.5.1.

## Updating to 0.9.0

Each sensor now answers one question; a register that only details or
explains another sensor's state is an attribute of that sensor. 32 entities
are gone, and the integration removes them from the entity registry on its
first start, so nothing stays behind as unavailable. Nothing needs deleting
or re-adding; the remaining entities keep their IDs, names and icons.

| Was an entity until 0.8.2 | Is now |
|---|---|
| **EVSE state**, **CP state** | `evse_state`, `cp_state` attributes of **Charging status** |
| **Error code** | `code`, `category` attributes of **Error** |
| **Heartbeat** (binary sensor) | `heartbeat` attribute (`ok`/`problem`) of **Connection quality** |
| **Voltage L1–L3**, **Power L1–L3** | `voltage_l1`–`voltage_l3` attributes of **Total power** (whole volts, only while power flows); a per-phase power is voltage × current |
| **Session duration**, **Session maximum current**, **Charging sessions** | `duration_minutes`, `maximum_current`, `sessions` attributes of **Session energy** |
| **Pending change due** | `due` attribute of **Pending change** |
| **Planned charging start** | `planned_start` attribute of **Timed charging status** |
| **Wallbox phase selection** | `switched` attribute of **Requested phases** |
| **Downgrade current** | `current` attribute of **Downgrade active** |
| **Energy manager fallback**, **Energy manager fallback current** | `behaviour`, `fallback_current` attributes of **Energy manager fallback active** |
| **Authorization enabled** | `enabled` attribute of **Authorization** |
| **Permanent cable lock** | `permanent` attribute of **Cable lock** |
| **Modbus layout version**, **Maximum house current**, **Maximum wallbox current**, **Phase rotation**, **Connected grid phases**, **Phase switching supported**, **Phase switching mode**, **Phase switching pause**, **Grid imbalance monitoring**, **Grid imbalance threshold**, **Sunshine+ minimum current** | state and attributes of the new **Wallbox settings** diagnostic sensor |

What to do after the update:

1. **Automations made from the bundled blueprints:** open them and re-select
   the changed input. *Charge from PV surplus* takes **Requested phases**
   instead of the phase-selection sensor, *Recover from a lost heartbeat* the
   **Error** binary sensor instead of the error-code sensor, and *Downgrade
   notification* no longer asks for a current entity.
2. **Templates and your own automations** read the attribute instead of the
   removed entity, for example
   `{{ state_attr('sensor.<wallbox>_session_energy', 'duration_minutes') }}`
   or a state trigger on `sensor.<wallbox>_connection_quality` with
   `attribute: heartbeat` and `to: problem`.
3. **Dashboards** that showed a removed entity need the attribute, for
   example an *Entity* card with `attribute: voltage_l1` on **Total power**.
4. **Recorder:** the history and statistics of the removed entities are not
   deleted; HA offers to remove orphaned statistics under **Settings → System
   → Repairs**. The profiles in the integration options exclude the phase
   currents (less detail) and signaled current, session energy and
   temperature (statistics only). Attributes compile no statistics.

Every remaining sensor is enabled by default; the voltage and per-phase power
sensors that new installations since 0.6.0 started disabled no longer exist.

## Updating to 0.8.0

In service mode, update the app to **0.8.0 first**, wait for initialization,
then update the integration. The integration's general minimum remains
**service 0.6.1 with API 2**, so ordinary connections keep working with an
older app. Virtual Plug & Charge, its manual release and the separate retry
count need app 0.8.0. Direct USB/LAN needs no app.

**Check your Control result automations.** Its event type now names the
command, e.g. `pause_failed`. An automation that filters `event_type` for
`failed`, `uncertain` or another outcome must filter the new `result`
attribute instead; see [React to command results](automations.md#react-to-command-results).

Everything new stays off until you set it up. An update does not create
vehicle profiles, enable automatic selection or Virtual Plug & Charge, or
change a LAN adapter or enable its TCP login. Sustained heartbeat or
service-connection failures now show Repairs notices that clear after recovery.

## Feature settings after an update

The integration's **Configure** menu has **Wallbox**, **Vehicles** and
**Charging costs**. Existing charge-level, departure, cost and loss settings
continue to work; you do not need to recreate them.

Optional [vehicle profiles](user-guide.md#vehicle-profiles) hold all settings
of a car: charge level and limit, next departure, charging losses and
detection signals. Link a profile to the car in BavarianData and it uses the
car's data right away; any area can still use manual entities. Existing
configurations continue without profiles. When you first add profiles, the old
settings are kept in a manual profile instead of being assigned to a guessed
car. **Use vehicle departure** is the display name of the former BMW button;
its entity identity and existing automations are kept. Generic HA entity
sources remain available for other vehicle integrations; manual and calendar
departure work without a vehicle source.

Automatic vehicle selection is switched on in the **Active vehicle** entity
and uses each profile's detection signals. Manual selection remains the
default.

Virtual Plug & Charge stays off until you switch on **Virtual Plug & Charge**
under the wallbox's **Configuration**. Every profile with detection signals
takes part. Follow the [setup steps](vehicle-setup.md#optional-virtual-plug--charge)
before enabling it; enabling may stop an existing charge while detection is
pending. Disabling it does not resume charging.

Departure planning has its own enable setting. Disabling it clears its active
trip and cancels its queued commands while keeping the percentage target and
the current pause state. See [feature setup](user-guide.md#configuration-and-options).

Module 3 offers **Enter or edit manually** or **Import for HA Home**. Opening its
settings alone makes no external lookup. Yearly refresh is a separate
optional choice after manual entry or import; it can replace saved manual prices
and windows with the new year's published tariff. It repeats the Home-coordinate
lookup, with daily retries if no usable tariff is available.

## Updating to 0.6.1

**Service mode requires app/service 0.6.1 or newer with API 2.** Update it first,
even if timed planning is not configured. Older apps, including 0.6.0b1, do not
provide the new timed-command expiry support. Direct USB/LAN needs no app.

Configure a vehicle SoC source and usable battery capacity before enabling the
new next-trip deadline. Manual entry, calendar events and optional BavarianData
timer import can coexist. Optional calendar and climate blueprints are described
under [Blueprints](blueprints.md); see [departure setup](user-guide.md#charge-for-the-next-departure)
for BMW timer sensors, weekdays and time zone. Ordinary charging targets still
only limit a charge. An active timed plan can resume a pause to meet departure.

The service drops queued timed-plan commands when their deadline passes, even
with HA offline. This does not reverse a command already applied. Recalculating
the plan and starting climate actions still require HA to be running.

German Module 2/3 settings now allow returning from Module 3 to Module 2 or
no adjustment. Module 3 can import the tariff for HA Home and optionally refresh
it at the year boundary. The lookup shares Home coordinates with VNBdigital;
see [Privacy](../PRIVACY.md) and [Charging estimates](charging-estimates.md).
The app also includes updated shared tariff validation.

Future integration and service versions can still differ when the service meets
the declared minimum.

## Updating to 0.6.0b1

The compatibility notes in this section describe that earlier beta. For the
current release, follow [Updating to 0.6.1](#updating-to-061) above.

Beta 0.6.0b1 improves entity grouping and state-dependent icons, fixes false
vehicle-disconnected states during wallbox faults, and names the display-only
solar selector **Solar display mode**. Existing entity IDs and user customizations
are retained; no device or entity needs deleting. If using the service, update
the app to **0.6.0b1** first to use its new capabilities, then update the integration.

Six optional phase-voltage/power sensors now start disabled on new installations;
existing registry settings are preserved. Session duration stops creating new
long-term statistics without deleting old data. Total energy remains suitable
for the Energy dashboard.

The new **Control result** event entity starts disabled; enable it for command
notifications when needed. Events work directly, or with a service that publishes the new
optional event feed. Older API 2 services remain compatible, but their event
entity is unavailable. Existing commands are not replayed as events after
startup or reconnect.

Version 0.6.0b1 also adds **Charging status**, **Connection quality**, **Cancel
pending change**, **Energy target** and **Energy target status**. A target only
limits energy and never starts charging. For service connections, target and
cancel support are optional capabilities: an older service remains usable, while
the new controls stay unavailable. Communication quality stays unknown if the
service does not publish it. A service update is needed for service-owned goals,
queued-command cancellation and communication quality. Charging status is
calculated in HA and also works with existing compatible service readbacks.

The vehicle percentage target is separately optional: select a vehicle SoC
entity in the integration options to add it. Selecting the car's own percentage
limit as a maximum is another optional setting. With no SoC source configured,
no percentage controls are added. See [Percentage targets](user-guide.md#optional-vehicle-percentage-target)
for behavior when the car or HA is unavailable. Existing kWh goals can recover
from temporary meter outages without resetting their baseline or lifting a
deliberate pause.

Optional [charging losses and costs](charging-estimates.md) use existing vehicle
and price data. They add no vehicle polling or growing history. All sources are
off by default; configure only the estimates you want. Service mode needs app
**0.6.0b1** for these measurements. Existing compatible apps retain their other functions.

### App connection page

App **0.6.0b1** adds **Open Web UI** for connection setup.
Start the app, open that page as an administrator and choose USB or LAN. USB
shows an adapter dropdown, preferring stable `/dev/serial/by-id/...` paths;
LAN shows the gateway host/IP and TCP port. Saving LAN also clears any old USB
path, so an unplugged adapter cannot block the change. A missing USB adapter is
never replaced automatically.

App **0.8.0** accepts **Supervisor 2026.10.4 or newer** for the administrator
access check; older apps required 2026.11.0. The integration's minimum Home
Assistant version is unchanged. On Supervisor versions older than 2026.10.4,
the native app **Configuration** tab remains available for manual setup.
Keep your existing integration entry.

Save, then restart the app from its **Info** tab. Saving does not change a running
connection. **Read-only**, current limits and polling intervals remain in the
native **Configuration** tab. Keep existing valid connection settings when
updating; the transport is not changed automatically.

With no serial path or no LAN host configured, app 0.6.0b1 remains running for
setup and its health check. It starts neither Modbus nor heartbeat nor discovery
until a valid connection is saved and the app restarted. A configured USB adapter
that is temporarily unplugged instead keeps its identity and is retried.
Older app **0.5.2b2** has no connection Web UI and uses the native fields
described [below](#app-connection-choice).

## 0.5.2b2: configurable charging transitions

Version 0.5.2b2 adds **Minimum time between state changes**, default **300
seconds** and configurable from **0 to 3600 seconds**. Early pause/resume,
phase, release and lock changes are queued; the latest charging request wins.
**A stop request also waits**, replacing any older pending start. **Pending
change** and its due time (a sensor of its own until 0.9.0, then the `due`
attribute) show what will happen and when. The regular
controls continue to show the wallbox's actual state.
While paused, the current slider instead shows the remembered resume target,
which may already reflect a pending current request. Pausing within five
seconds of a current write is queued too and does not need to be requested again.

Before a phase change, version 0.5.2b2 waits up to 30 seconds for live confirmation
that the vehicle has paused, then adds a configurable grace time (default
**5 seconds**, range **0–30 seconds**) and checks the state again. A failed
confirmation or timeout leaves the phase command unsent and retains the pause.
The grace time is separate from the 300-second state-change interval and is set
in the same place.

When fresh EVSE Idle and CP A1/A2 readings confirm that no vehicle is connected,
the longer state-change wait and vehicle grace time are skipped. Pending waits
check for unplugging every five seconds. The five-second register-write guards
and heartbeat remain active. Unknown or inconsistent status, read failures and
older layouts without CP retain the longer state-change wait.

In direct mode, configure the interval in the integration options. In service
mode, the app/service must include this feature and owns the setting; updating
only HA cannot add it to an older service. Compatible older services keep their
existing timing behavior. Update both components for the new service-mode
setting and status display, with the app/service first.

After a direct-entry reload or an app/service restart, pending work is cancelled;
request it again if still wanted. Restarting HA Core alone does not cancel a
running service's queue. Check pending status before that restart because its
command can execute while HA is offline. See the
[user guide](user-guide.md#waiting-between-charging-changes)
for the interval, stop behavior and manufacturer recommendation.

## Routine updates

Back up Home Assistant, read the offered release notes and follow
[Installation → Updates](installation.md#updates). Complete the requested Core
restart and check the existing wallbox's readings and heartbeat afterward.

The integration and service have independent versions. **Integration 0.6.1
requires service 0.6.1 or newer with API 2.** After this required service update,
future integration-only updates can reuse a compatible app without restarting
it. Core can restart while that service continues its heartbeat and saved
charging intent with writes enabled.

If an app update is offered or the integration raises its minimum service
requirement, follow the release notes. **When both need updating, update the
app or standalone service first**, then the integration and restart Core.
An app or host restart interrupts the heartbeat. Older 0.5.0 beta installations
need service 0.6.1 first; their old service API also differs.
See the [service guide](modbus-service.md) for the current release channel and
update instructions. Do not add the wallbox again to refresh its credentials or
restore missing controls.

After blueprint changes, follow [Updating existing blueprint automations](blueprints.md#updating-existing-blueprint-automations).

## App connection choice

App **0.5.2b2** has exactly three choices. If your saved choice already has a
readable name, keep it. Otherwise select the matching name once:

| Older saved value | Select |
|---|---|
| `serial` | **USB / RS-485** |
| `rtu_over_tcp` | **LAN: Modbus RTU (TCP)** |
| `modbus_tcp` | **LAN: Modbus TCP** |

In app 0.5.1, select and save the readable name **before updating**. Retain the
adapter/IP, port and other settings. Supervisor skips automatic updates while
the saved choice is incompatible. If a manual app update rejects an old value,
the **Configuration** tab remains available: select the matching name, save,
and start the app. There is no automatic USB fallback and no need to reinstall
the app or remove the HA entry. Standalone JSON configurations continue to
accept their existing technical names.

## Optional move to the service

Direct USB and LAN connections remain supported. On Home Assistant OS, the independent
service app is recommended when the heartbeat should continue through Core
restarts. Switching is optional and retains the existing wallbox entry.

Use [Move an existing wallbox](modbus-service.md#move-an-existing-wallbox) for the
adapter handover and connection change. Follow those steps before starting the
app; both connections must not operate the bus together.

After the move, write permission follows the app's **Read-only** switch. HA has
no additional control-mode selector for service entries. With a direct USB or LAN
connection, enable writes using **Control mode → Modbus master** in the entry
options. See [First setup](quickstart.md#6-check-readings-then-enable-control)
for checking readings and enabling control.
