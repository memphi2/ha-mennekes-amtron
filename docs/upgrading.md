# Updating an existing installation

**Keep your existing wallbox entry.** Routine updates
do not require deleting devices, entities or automations. Moving from HACS to
the Marketplace also keeps the same integration and entry.

## Routine updates

Back up Home Assistant, read the offered release notes and follow
[Installation → Updates](installation.md#updates). Complete the requested Core
restart and check the existing wallbox's readings and heartbeat afterward.

With the service, **update the app or standalone service first**, then update
the integration and restart Core. Both must be on **0.5.0**. The old beta
integration may temporarily show unavailable controls until both updates are
complete; the updated service keeps its heartbeat and charging intent while HA
updates. A Core restart leaves the running app active; an app or host restart
interrupts its heartbeat.
See the [service guide](modbus-service.md) for the current release channel and
update instructions. Do not add the wallbox again to refresh its credentials or
restore missing controls.

After blueprint changes, follow [Updating existing blueprint automations](blueprints.md#updating-existing-blueprint-automations).

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
