# Updating an existing installation

**Keep your existing wallbox entry.** Routine updates
do not require deleting devices, entities or automations. Moving from HACS to
the Marketplace also keeps the same integration and entry.

**Testing 0.5.2b2?** Follow [Beta testing](installation.md#beta-testing-052b2)
to select the integration prerelease and beta app catalog. Check the
[app connection choice](#app-connection-choice) before updating the app.

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

The integration and service have independent versions. **Service 0.5.0 with
API 2 remains compatible**; a routine integration update does not require an
app update or restart. Core can restart while the compatible service continues
its heartbeat and saved charging intent with writes enabled.

If an app update is offered or the integration raises its minimum service
requirement, follow the release notes. **When both need updating, update the
app or standalone service first**, then the integration and restart Core.
An app or host restart interrupts the heartbeat. Older 0.5.0 beta installations
need an API 2 service first because their service API differs.
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
