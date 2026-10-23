# Upgrading from 0.1.0 to 0.2.0

Version 0.2.0 uses the Home Assistant integration domain `mennekes_amtron_rtu`
and appears as **MENNEKES AMTRON RTU (Unofficial)**. Version 0.1.0 used
`mennekes_amtron`, which is also the domain of the separate Modbus TCP
integration `nobelp/mennekes-amtron-ha`.

This is a breaking change. Existing configuration entries, device identities
and entity registrations are not migrated automatically. The wallbox must
be added again. A backup is the way back if you need the original setup.
These steps apply only to the RTU integration from
`memphi2/ha-mennekes-amtron`; leave a TCP installation from `nobelp` alone.

## Before updating

1. Create a full Home Assistant backup. Record the serial port, device
   address, baud rate, frame, polling interval, control mode and current cap.
   Save your entity IDs, automation settings and any edited blueprints.
2. Stop charging and disable automations that control this wallbox. Removing
   a master-mode entry stops its heartbeat. The wallbox can then enter the
   energy-manager fallback/error state; see [Safety](safety.md) and configure
   the fallback with the manufacturer's tool as appropriate for your setup.
3. Check `custom_components/mennekes_amtron/manifest.json` before removing
   files: its `documentation` URL must identify `memphi2/ha-mennekes-amtron`.
   If it identifies `nobelp/mennekes-amtron-ha`, leave that directory and its
   HACS installation alone. In *Settings → Devices & services*, delete only
   this project's old RTU config entry. If the old files belong to this RTU
   project, uninstall **memphi2/ha-mennekes-amtron** through HACS while 0.1.0
   is still installed, or remove the old directory for a manual installation.
4. Restart Home Assistant to unload the old integration.

## Install and reconfigure

1. Install 0.2.0 following [Quick start](quickstart.md#3-install), then restart
   Home Assistant. Manual installs now use
   `custom_components/mennekes_amtron_rtu/`.
2. Add **MENNEKES AMTRON RTU (Unofficial)** with the recorded bus settings.
   New entries start in read-only mode. Check the readings, then restore
   your options and enable *Modbus master* when ready.
3. Check the new entity IDs and update dashboards, scripts and automations.
   You can assign the recorded entity IDs to the corresponding new entities
   if those IDs are free. Re-select device targets: the device registration
   has changed even when an entity ID is reused. Check the Energy dashboard
   and statistics; preservation of history is not guaranteed by this change.
4. Replace action calls to `mennekes_amtron.set_charging_current` with
   `mennekes_amtron_rtu.set_charging_current`. Update any integration filters
   and debug logger configuration to the new domain/package as well.
5. New blueprints are installed under
   `blueprints/automation/mennekes_amtron_rtu/`. For each existing blueprint
   automation, change its `use_blueprint.path` from `mennekes_amtron/<file>.yaml`
   to `mennekes_amtron_rtu/<file>.yaml` and check all input entities, or recreate
   the automation from the new blueprint. Keep your customized copies and
   port their changes deliberately. The integration leaves the old blueprint
   directory untouched; remove obsolete copies only after no automation
   references them.
6. Test the controls and automations, then re-enable the automations you
   disabled for the upgrade.

If you already updated the files, the old RTU entry may show as unavailable.
Back it up, record its settings and remove that entry before adding the new
one. Remove any leftover old package only after checking the repository URL
in its manifest as above, then restart Home Assistant.
