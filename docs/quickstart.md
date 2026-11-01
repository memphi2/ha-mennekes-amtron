# Quick Start

## 1. Hardware

Cabling, DIP switches, the §14a downgrade input and USB passthrough are in
**[hardware.md](hardware.md)**. The two things you cannot skip:

- Modbus `A` is `+`, `B` is `−`, and `GND` has to be connected.
- The wallbox has to be in **Modbus satellite mode**: bank S1, DIP 4 and
  DIP 5 to ON, then restart it. Without that it answers nothing.

Modbus RTU uses a **single master**. If another energy manager or a §14a control
box already drives it, do not set up this integration on that bus.
Read-only mode also sends requests. See [safety.md](safety.md).

## 2. Install

**Existing 0.1.0 installation:** follow [Upgrading](upgrading.md) first.
The integration domain changes in 0.2.0 and the wallbox must be added again.

On **Home Assistant 2026.11+**, use **Settings → Marketplace**. On
**2026.9/2026.10**, use **HACS**. Follow [Installation](installation.md) to
install **MENNEKES AMTRON RTU (Unofficial)** by **memphi2**, including the
custom-repository step while catalog inclusion is pending.

The first Marketplace installation normally needs no restart. HACS installs
and updates of already loaded integration code need a restart.

## 3. Find the adapter

The adapter is plugged into the machine Home Assistant runs on, so this has to
run there too:

| Installation | Command |
|---|---|
| Home Assistant OS / Supervised | `docker exec homeassistant ls -l /dev/serial/by-id/` |
| Home Assistant Container | `docker exec homeassistant ls -l /dev/serial/by-id/` |
| Home Assistant Core | `ls -l /dev/serial/by-id/` |

On Home Assistant OS, open a shell with the *Advanced SSH & Web Terminal*
add-on and turn its protection mode off, otherwise the add-on cannot reach
`docker`.

Use the `/dev/serial/by-id/...` path, not `/dev/ttyUSB0`: the `ttyUSB` number
changes when devices are re-enumerated, the `by-id` path does not.

## 4. Prove the bus before writing anything

The integration ships the check, so after step 2 it is already on the machine
with the adapter, at
`/config/custom_components/mennekes_amtron_rtu/smoke_modbus.py`. Home Assistant's
own Python already has pymodbus, so nothing has to be installed:

| Installation | Command |
|---|---|
| Home Assistant OS / Supervised / Container | `docker exec -it homeassistant python /config/custom_components/mennekes_amtron_rtu/smoke_modbus.py <port>` |
| Home Assistant Core | `/srv/homeassistant/bin/python /config/custom_components/mennekes_amtron_rtu/smoke_modbus.py <port>` |

Run this check before adding an active entry: it needs exclusive bus and port
access. It reads registers without writing them or sending a heartbeat.
A good run prints:

```text
port             /dev/serial/by-id/usb-...
bus              57600 baud, 8N2
device address   50
modbus layout    v01.03
firmware         2023.21.11024
serial number    <your serial>
article number   1313201205
evse state       1 (idle, no vehicle connected)
max EVSE current 16.0 A

The bus is fine. Add the integration with these values.
```

If you do not know the device address, or somebody changed the bus parameters
with the MENNEKES configuration tool, let it search:

```bash
docker exec -it homeassistant \
  python /config/custom_components/mennekes_amtron_rtu/smoke_modbus.py \
  /dev/serial/by-id/<your-adapter> --scan --scan-baudrate
```

`--scan` tries every documented device address from 10 to 50, and
`--scan-baudrate` every documented baud rate as well. It prints how long that
will take before it starts.

If it fails, the cause is wiring, bus parameters or the device address — not
Home Assistant.

## 5. Add the wallbox

*Settings → Devices & services → Add integration → MENNEKES AMTRON RTU (Unofficial).*

Pick the port, confirm the device address and the bus parameters. The flow
opens the port and reads the device before it creates the entry, so a wrong
value fails here instead of producing an empty device.

**If you do not know the address or the parameters**, leave the last field at
*Search every device address*. When the values you entered do not work, the
flow tries every documented device address at your bus parameters — about
twenty seconds — and offers what it found. *Search every address, baud rate
and frame* widens that to every documented combination and takes several
minutes. Both send read requests and require exclusive bus access; they do
not write registers or send a heartbeat.

## 6. Start in read-only mode

A new entry is **read-only**: it polls and creates sensors, but it never
writes and it sends no heartbeat. Check that the sensors look plausible
against the wallbox display.

When you are satisfied, open the entry options and switch *Control mode* to
**Modbus master**. The entry reloads, the heartbeat task starts and the
control entities appear.

## 7. First charge

1. Set `number.<name>_charging_current_limit` to 6 A.
2. `sensor.<name>_signaled_current` has to follow within a few seconds.
3. `sensor.<name>_cp_state` goes to `C2`, `sensor.<name>_evse_state` to
   `Charging`.
4. Raise the limit. Leave at least five seconds between changes — the
   integration defers faster changes rather than rejecting them, but the
   wallbox still only accepts one every five seconds.

## Removing the integration

Follow [Removal](installation.md#removal). Deleting an entry stops its
heartbeat and closes the serial port; check fallback behavior before doing so.
