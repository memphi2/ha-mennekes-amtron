# Updating an existing installation

**Keep your existing wallbox entry.** Routine updates of version 0.2.0 or newer
do not require deleting devices, entities or automations. Moving from HACS to
the Marketplace also keeps the same integration and entry.

The old **0.1.0** release is the exception; its domain migration is described
[below](#legacy-010-domain-migration).

## Routine updates

Back up Home Assistant, read the offered release notes and follow
[Installation → Updates](installation.md#updates). Complete the requested Core
restart and check the existing wallbox's readings and heartbeat afterward.

With the service, the integration and app are separate updates. A Core restart
leaves the running app active; an app or host restart interrupts its heartbeat.
See the [service guide](modbus-service.md) for the current release channel and
update instructions. Do not add the wallbox again to refresh its credentials or
restore missing controls.

After blueprint changes, follow [Updating existing blueprint automations](blueprints.md#updating-existing-blueprint-automations).

## Optional move to the service

Direct serial mode remains supported. On Home Assistant OS, the independent
service app is recommended when the heartbeat should continue through Core
restarts. Switching is optional and retains the existing wallbox entry.

Use [Move an existing wallbox](modbus-service.md#move-an-existing-wallbox) for the
adapter handover and connection change. Follow those steps before starting the
app; both connections must not operate the bus together.

After the move, write permission follows the app's **Read-only** switch. HA has
no additional control-mode selector for service entries. With a direct serial
connection, enable writes using **Control mode → Modbus master** in the entry
options. See [First setup](quickstart.md#6-check-readings-then-enable-control)
for checking readings and enabling control.

## Legacy 0.1.0 domain migration

Only version **0.1.0** used the domain `mennekes_amtron`. Since 0.2.0, this
project uses `mennekes_amtron_rtu`, displayed as **Mennekes Amtron RTU**. The old
domain is also used by the separate Modbus TCP integration
`nobelp/mennekes-amtron-ha`; leave that installation alone.

This old domain change cannot migrate config entries, device identities or
entity registrations automatically. For this migration only, the RTU wallbox
must be added again. A backup is the way back to the original installation.

### Before replacing 0.1.0

1. Create a full Home Assistant backup. Record the serial and bus settings,
   polling intervals, control mode, current cap and entity IDs. Save automation
   settings and any edited blueprints.
2. Stop charging and disable wallbox-control automations. Check the configured
   [fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it)
   before stopping the heartbeat.
3. Check `custom_components/mennekes_amtron/manifest.json`: its `documentation`
   URL must identify `memphi2/ha-mennekes-amtron`. If it identifies
   `nobelp/mennekes-amtron-ha`, leave that directory and managed installation alone.
   Delete only this project's old RTU config entry. Uninstall this project's
   0.1.0 package through its installation manager, or remove the verified old
   directory for a manual installation.
4. Restart Home Assistant to unload the old package.

### Install and reconnect

1. Follow [Installation](installation.md) and complete any requested restart.
   Manual installations now use `custom_components/mennekes_amtron_rtu/`.
   Follow [Quick start](quickstart.md) to add the wallbox with the recorded
   settings and your chosen connection. Check readings before enabling writes.
2. Check the new entity IDs and update dashboards, scripts and automations.
   You may reuse the old entity IDs where free, but must re-select device
   targets because the device registration changed. Check the Energy dashboard
   and statistics; history preservation is not guaranteed for this migration.
3. Replace `mennekes_amtron.set_charging_current` action calls with
   `mennekes_amtron_rtu.set_charging_current`. Update integration filters and
   debug logger configuration that use the old domain/package.
4. For each blueprint automation, change `use_blueprint.path` from
   `mennekes_amtron/<file>.yaml` to `mennekes_amtron_rtu/<file>.yaml` and check its
   inputs, or recreate it from the new blueprint. Keep customized copies and
   port their changes deliberately. Remove old blueprint copies only after no
   automation references them.
5. Test the controls and automations, then re-enable the automations you stopped.

If you already replaced the files, back up and record the unavailable old RTU
entry before removing it. Check the repository URL in any remaining old package
as described above before removing that package, then restart Home Assistant.
