# Quick start

This walkthrough takes you from an installed adapter to the first controlled
charge. Normal setup uses the Home Assistant UI; a command-line bus check is
optional.

## 1. Check the hardware

Have a qualified electrician prepare the wallbox using its installation
manual and the [hardware guide](hardware.md). It must support Modbus RTU,
have the RS-485 adapter connected, and be in **Modbus satellite mode**
(bank S1, DIP 4 and DIP 5 on, followed by a wallbox restart).

Confirm **exclusive bus access**. Another energy manager, control box or Home
Assistant instance must not drive the same bus. Read-only mode also sends
requests; it is not a passive listener.

## 2. Install

Install **MENNEKES AMTRON RTU (Unofficial)** by **memphi2** using the
[installation guide](installation.md): Marketplace on Home Assistant 2026.11+,
HACS on 2026.9/2026.10. That guide includes the custom-repository fallback and
explains when a restart is needed.

For an existing **0.1.0** installation, follow [Upgrading](upgrading.md) first.
Existing **0.2.x** entries can be kept.

## 3. Make the adapter available

Connect the USB RS-485 adapter to the machine running Home Assistant. With a
virtual machine or container, pass it through to Home Assistant; see
[USB passthrough](hardware.md#making-the-adapter-visible-to-home-assistant). The setup form lists available
ports and prefers stable `/dev/serial/by-id/...` paths. Use one when available:
`/dev/ttyUSB0` can change after reconnecting USB devices.

If the adapter is missing from the list, check passthrough and permissions
before changing wallbox settings. You can enter a port manually in the form.

## 4. Check fallback behavior

Read [Safety and limitations](safety.md), especially the energy-manager
fallback. In master mode, the wallbox depends on a heartbeat from Home
Assistant. A restart, lost USB connection or integration removal stops it.
Configure the intended fallback with the manufacturer's tool before enabling
control. Keep the vehicle disconnected during the initial control setup.

## 5. Add the wallbox

Open **Settings → Devices & services → Add integration → MENNEKES AMTRON
RTU (Unofficial)**. Select the adapter and enter the configured bus settings.
Factory defaults are:

| Setting | Value |
|---|---|
| Device address | `50` |
| Baud rate | `57600` |
| Data bits / parity / stop bits | `8` / `N` / `2` |

The form verifies communication before creating an entry. If the values are
unknown, use its search option: search addresses at the chosen bus settings
first, or all documented baud rates and frames if needed. The broader search
can take several minutes. Searches send read requests without register writes
or a heartbeat and still require exclusive bus access.

## 6. Verify readings, then enable control

New entries start in **read-only** mode: sensors are available, controls and
the heartbeat are disabled. Compare the readings with the wallbox display.
Read-only mode does not take over an energy-manager heartbeat.

When readings are plausible and fallback is configured, open the entry
options. Set the **charging current cap** for your installation and change
**Control mode** to **Modbus master**. The entry reloads, starts the heartbeat
and adds the controls. Resolve any repair messages before charging.

## 7. First charge

1. Set **Charging current limit** to 6 A before connecting the vehicle.
2. Connect the vehicle and complete any required authorization. Check that
   the wallbox is unlocked and charging is released.
3. Check **Charging**, **EVSE state**, **Signaled current** and **Total power**.
   Actual charging also depends on the vehicle; a connection alone does not
   guarantee that it will draw current.
4. Raise the limit as appropriate. Faster changes are deferred to respect the
   five-second write interval.
5. Test **Charging paused**: on pauses; off resumes after the waiting interval.
   Allow more than five minutes between pause and resume. Do not set 0 A to stop.

Once basic operation is verified, follow the [user guide](user-guide.md),
configure [Recorder](recorder.md) and add [blueprint automations](blueprints.md).

## Optional command-line bus check

Use this only for troubleshooting, before adding an active entry or after
unloading it. The check needs exclusive use of the serial port and bus; do not
run it alongside the integration or another Modbus master.

From a host administration shell with access to the Home Assistant container:

```bash
docker exec -it homeassistant \
  python /config/custom_components/mennekes_amtron_rtu/smoke_modbus.py \
  /dev/serial/by-id/REPLACE_WITH_YOUR_ADAPTER
```

Adjust the container name and config path if needed. The installed integration
includes the script and its dependencies. It reads identification and state
registers without writing or sending a heartbeat. `--scan` searches addresses;
`--scan-baudrate` extends that search to bus settings. A failure can mean port
access, wiring, conflicting bus use, unsupported responses or incorrect settings.
See [Troubleshooting](troubleshooting.md).

The output includes device identifiers. Remove the serial number and full
adapter path before sharing it publicly. This check is optional; do not enable
host-shell access solely to complete normal UI setup.

## Removing the integration

Follow [Removal](installation.md#removal). Deleting an entry stops its
heartbeat and closes the serial port; check fallback behavior first.
