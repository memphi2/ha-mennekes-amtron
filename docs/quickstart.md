# Quick Start

## 1. Hardware

You need a USB RS-485 adapter on the Home Assistant host and three wires to
the wallbox.

| Wallbox | Meaning | Adapter |
|---|---|---|
| Modbus `A` | `+` | `A` / `D+` |
| Modbus `B` | `−` | `B` / `D−` |
| `GND` | reference | `GND` |

RS-485 is a **single-master** bus. If another energy manager or a §14a control
box already drives this bus, do not add a second master — keep this
integration in read-only mode. See [safety.md](safety.md).

## 2. Enable the Modbus satellite mode

On the wallbox, set **DIP bank S1, DIP 4 and DIP 5 to ON** and restart it.
Without that the wallbox answers nothing and rejects every write.

The factory bus parameters are:

```text
57600 baud, 8 data bits, 2 stop bits, no parity
Modbus device address 50 (10-50 configurable)
Byte and word order: big endian
```

All of them can be changed with the MENNEKES configuration tool; the config
flow asks for whatever you actually use.

## 3. Find the adapter

```bash
ls -l /dev/serial/by-id/
```

Use the `/dev/serial/by-id/...` path, not `/dev/ttyUSB0`: the `ttyUSB` number
changes when devices are re-enumerated, the `by-id` path does not.

## 4. Prove the bus before writing anything

```bash
python scripts/smoke_modbus.py /dev/serial/by-id/<your-adapter>
```

The script is read-only and sends no heartbeat, so it cannot put the wallbox
into the energy-manager error state. A good run prints:

```text
modbus layout    v01.03
serial number    <your serial>
article number   1313201205
max EVSE current 16.0
```

If it fails, the cause is wiring, bus parameters or the device address — not
Home Assistant.

## 5. Install

**HACS (recommended)**

1. HACS → three-dot menu → *Custom repositories*.
2. Add this repository, category *Integration*.
3. Install, then restart Home Assistant.

**Manual**

Copy `custom_components/mennekes_amtron` into your Home Assistant
`config/custom_components/` directory and restart.

## 6. Add the wallbox

*Settings → Devices & services → Add integration → MENNEKES AMTRON.*

Pick the port, confirm the device address and the bus parameters. The flow
opens the port and reads the device before it creates the entry, so a wrong
value fails here instead of producing an empty device.

## 7. Start in read-only mode

A new entry is **read-only**: it polls and creates sensors, but it never
writes and it sends no heartbeat. Check that the sensors look plausible
against the wallbox display.

When you are satisfied, open the entry options and switch *Control mode* to
**Modbus master**. The entry reloads, the heartbeat task starts and the
control entities appear.

## 8. First charge

1. Set `number.<name>_charging_current_limit` to 6 A.
2. `sensor.<name>_signaled_current` has to follow within a few seconds.
3. `sensor.<name>_cp_state` goes to `C2`, `sensor.<name>_evse_state` to
   `Charging`.
4. Raise the limit. Leave at least five seconds between changes — the
   integration defers faster changes rather than rejecting them, but the
   wallbox still only accepts one every five seconds.

## Removing the integration

*Settings → Devices & services → MENNEKES AMTRON → three-dot menu → Delete.*

Deleting the entry stops the heartbeat and closes the serial port. The
wallbox keeps the last values it received; if you want it to fall back to a
defined state instead, configure an energy-manager fallback current with the
MENNEKES configuration tool before removing the integration.

To remove the files as well, uninstall it in HACS or delete
`custom_components/mennekes_amtron`.
