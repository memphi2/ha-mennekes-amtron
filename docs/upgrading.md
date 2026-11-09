# Updating an existing installation

**Keep your existing wallbox entry.** Routine updates
do not require deleting devices, entities or automations. Moving from HACS to
the Marketplace also keeps the same integration and entry.

**Current stable release: 0.6.1.** In service mode, update the app/service first,
then the integration. Keep existing app data and your HA entry. The **#apps**
catalog carries stable updates; **#apps-beta** follows the beta channel below.
Check the [app connection choice](#app-connection-choice) before updating from 0.5.1.

## Trying 0.8.0b8

**0.8.0b8 is a beta of the integration and app; 0.6.1 remains stable.** Opt into prereleases in your integration installer and select
**v0.8.0b8**. Keep the
existing HA entry. The beta app is offered through
`https://github.com/memphi2/ha-mennekes-amtron#apps-beta`; existing users of that
catalog can use **Check for updates** and update their app in place.

Moving from **#apps** to **#apps-beta** creates a separate app installation.
Follow the [channel handover](modbus-service.md#updates), preserving its data
and ensuring only one service owns the wallbox connection. In service mode,
update the app to **0.8.0b8 first**, wait for initialization, then update the
integration. Beta 8 detects a BMW i4 or i5 by its charging port status, so
detection from the vehicle and automatic selection work for these cars, and a
reached percentage target pauses only with a car at the wallbox. Beta 7 starts a planned departure late at full current and
finishes at 6 A to reach the target at departure, instead of charging slowly
from many hours before; each vehicle profile remembers its charging phases for
that plan. With **Switch off charging release when unplugged**, unplugging now
also ends a manual pause, and switching the release during a pause no longer
waits for the state-change interval. It also logs a single lost Modbus reply no longer as an error and
counts it as a retry (app **0.8.0b7** for service mode). Beta 6 offers BavarianData again when adding or editing a vehicle
profile and moves every vehicle setting into the car's profile under
**Configure → Vehicles**; automatic selection is switched on in the **Active
vehicle** entity. A percentage target at the car's own limit no longer pauses
the wallbox when reached (app **0.8.0b6** for service mode): the car ends that
charge itself and nothing changes until you unplug. Virtual Plug & Charge no
longer loses its grant for the rest of a connection when the wallbox status is
briefly unreadable or contradictory. Beta 5 keeps each car's upcoming departure when you switch cars
and accepts a text sensor as the cable signal for automatic selection; it needs
no app update. Beta 4 keeps entities available across a single disturbed Modbus
read (app **0.8.0b4** for service mode), keeps the selected car after unplugging
instead of reloading, remembers each car's percentage target and no longer lifts
a pause after a restart with the car still connected. Beta 3 lets Virtual
Plug & Charge end a manual pause when a recognized car is plugged in. App
**0.8.0b3** lifts that pause while the charging release is
still off, so the release follows without the state-change interval; with an
older app the release can wait for that interval. The status sensor names the
reason when charging stays blocked. Beta 2's separate **Debug log** tab, wallbox
Plug & Charge switch and guided vehicle setup remain. Supervisor **2026.10.4+**
remains supported.
The integration's general minimum remains **service 0.6.1 with API 2**; ordinary
connections can keep using a compatible older service. Virtual Plug & Charge
needs the new app capability. Matching version numbers are not generally required.

Adapter configuration and TCP login remain optional. An update does not change
the LAN adapter or enable its authentication. Use [LAN adapter setup](lan-adapters.md)
only when you want to configure a compatible adapter. Sustained heartbeat or
service-connection failures now show Repairs notices that clear after recovery.

## Feature settings after an update

**Beta 0.8.0b1** adds the following profile, automatic-selection,
Virtual Plug & Charge and configuration improvements. Existing sources remain
usable until you deliberately adopt the new setup.

The integration's **Configure** menu separates **Wallbox**, **Vehicle**,
**Next departure**, **Charging costs** and **Charging losses**. Each section
shows the inputs for the features you enable and saves independently.
Existing SoC, departure, cost and loss settings continue to work; you do not
need to recreate them.

Optional [vehicle profiles](user-guide.md#vehicle-profiles) let you choose
BavarianData and the car once, then enable its supported features separately.
Manual selection remains the default. Automatic selection from cable and home
signals is separately optional; an update does not enable it. Existing
configurations continue without profiles, including independent vehicles for departure and charging history and
the existing loss average. When you first add profiles, the old settings are
retained in a manual profile instead of being assigned to a guessed car.
**Use vehicle departure** is the new display name of the former BMW button;
its entity identity and existing automations are retained. BavarianData is the
first implemented vehicle adapter. Generic HA entity sources remain
available for other vehicle integrations; manual and calendar departure work
independently of a provider.

Virtual Plug & Charge is separately optional and stays off until you enable it.
An existing activation setting is preserved. From **0.8.0b2**, activation uses
the wallbox's **Virtual Plug & Charge** switch
under **Configuration**. It needs detection sources for each participating
profile (before 0.8.0b6 also a separate permission per profile);
automatic profile selection alone does not enable it. Service mode also needs
app/service **0.8.0b1 or newer** with the charging-permission capability. Follow the [setup steps](vehicle-setup.md#optional-virtual-plug--charge)
before enabling it; enabling may stop an existing charge while detection is
pending. Disabling the feature does not resume charging.

Departure planning has its own enable setting. Disabling it clears its active
trip and cancels its queued commands while preserving the ordinary percentage
target and current pause state. See [feature setup](user-guide.md#configuration-and-options).

Module 3 now offers **Enter or edit manually** or **Import for HA Home**. Opening its
settings alone makes no external lookup. Yearly refresh is a separate
optional choice after manual entry or import; it can replace saved manual prices
and windows with the new year's published tariff. It repeats the Home-coordinate
lookup, with daily retries if no usable tariff is available. Existing choices
are retained; opening settings alone does not enable it.

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
the declared minimum. Stable 0.6.1 includes the earlier beta improvements below.

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

App **0.8.0b1** accepts **Supervisor 2026.10.4 or newer** for the administrator
access check. Older apps through 0.7.0b4 had an overly strict 2026.11.0 requirement;
update the beta app to receive the correction. The integration's minimum Home
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
change** and **Pending change due** show what will happen and when. The regular
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
