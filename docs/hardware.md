# Hardware Installation

Wiring, DIP switches and making the adapter visible to Home Assistant.

> **Have an electrician do the work on the wallbox.** Opening an AMTRON means
> working on a fixed electrical installation. De-energize it first, follow the
> manufacturer's installation manual, and follow your local regulations. This
> document describes the Modbus side of an installation that somebody
> qualified has already made safe.

## Where the facts come from

Two kinds of statement appear below, and they are marked:

- **(Spec)** — the MENNEKES Modbus RTU specification, revision 2.5, register
  layout v01.03. The same document [docs/modbus-registers.md](modbus-registers.md)
  is built from.
- **(Manual)** — the installation and operating manual of the device, which is
  **not** part of this repository. Where a detail depends on your exact model
  or on a DIP numbering this project cannot verify, it says so instead of
  guessing.

Everything about RS-485 cabling is ordinary field-bus practice, not a vendor
claim.

## What you need

- A **USB RS-485 adapter** on the machine Home Assistant runs on. Any adapter
  with a reliable driver works; the integration talks plain Modbus RTU through
  pyserial. There is no supported-adapter list, because the protocol does not
  care.
- **Shielded twisted-pair cable** for the A/B pair, with a third conductor or
  the shield as the ground reference.
- Possibly **two 120 Ω termination resistors**, see below.

## Connecting the bus

**(Spec)** Modbus `A` is `+`, Modbus `B` is `−`.

| Wallbox | Adapter |
|---|---|
| `A` (`+`) | `A` / `D+` |
| `B` (`−`) | `B` / `D−` |
| `GND` | `GND` |

Connect the ground. RS-485 is differential, but the two ends still need a
common reference; a floating pair is the classic source of a bus that works on
the bench and drops frames in the installation.

Ordinary RS-485 practice for the rest:

- **One line, not a star.** Run the cable from device to device. Keep stubs
  short.
- **Terminate both physical ends** with 120 Ω if the run is long or the baud
  rate high. On a short cable between a wallbox and an adapter in the same
  cabinet, termination is usually unnecessary and sometimes harmful. If frames
  drop, try it.
- **Keep away from power cables.** Cross them at a right angle rather than
  running parallel.
- **Ground the shield at one end only**, to avoid a ground loop.

If the bus misbehaves, lowering the baud rate with the MENNEKES configuration
tool buys a lot of margin and costs nothing here: the integration polls a few
hundred registers every few seconds, which is tiny.

## DIP switches

**Every DIP change needs a restart of the wallbox to take effect.** (Spec, for
the satellite switches; the manual says the same for the others.)

### Modbus satellite mode — required

**(Spec)** The wallbox speaks Modbus only when it is configured as a
*satellite*: **bank S1, DIP 4 and DIP 5 to ON**, then restart.

Without it the wallbox answers nothing, or answers reads and refuses writes.
That is the single most common reason the setup fails, and the integration
raises a repair issue naming it.

### Installation current — bank S2, DIP 6–8

**(Spec)** The maximum current of the house connection is set with **DIP 6, 7
and 8 on bank S2**. The three switches encode a value from 0 to 7:

| Value | Current |
|---|---|
| 0 | 63 A |
| 1 | 50 A |
| 2 | 40 A |
| 3 | 35 A |
| 4 | 32 A |
| 5 | 25 A |
| 6 | 20 A |
| 7 | 16 A |

**(Manual)** Which switch is the most significant bit is in the installation
manual. This project does not reproduce that mapping, because getting it
backwards would set 63 A where 16 A was meant.

Whatever you set, read it back afterwards:
`sensor.*_max_current_house` reports what the wallbox understood.

### §14a EnWG downgrade — bank S2, DIP 4+5

**(Manual)** The reduced current during a grid-operator downgrade is set as a
**percentage of the maximum charging current** with DIP 4 and DIP 5 on bank
S2: 0 %, 25 %, 50 % or 75 %, with 6 A as the floor.

Read it back with `sensor.*_downgrade_current`.

### Solar mode — DIP 7

**(Spec)** The solar charging mode has to be enabled with **DIP 7** before the
`select.*_solar_charging_mode` entity has any effect. The specification names
the switch but not its bank; check the installation manual.

## The §14a EnWG downgrade input

**(Manual)** The grid operator's dimming signal reaches the wallbox through
its own hardware input, **XG1**: a potential-free contact, 12 V DC / 8 mA,
with the default logic *normally closed* — a closed contact means the
downgrade is active.

This matters for the integration in two ways:

**It cannot be bypassed.** The wallbox applies the downgrade internally and
limits the charging current to the smallest of the energy-manager current, the
downgrade current, the maximum EVSE current and the maximum house current.
**(Spec)**: values written over Modbus are automatically limited by the
configuration of the wallbox. The integration makes the state visible through
`binary_sensor.*_downgrade_active` and `sensor.*_downgrade_current`, and
nothing more.

**It decides whether you may be the master.** If your §14a control box is
wired to XG1, it is not on the Modbus bus and there is no conflict. If instead
it is itself a Modbus master on the same RS-485 pair, then this integration
has to stay in read-only mode: RS-485 allows exactly one master. Check this
before switching the control mode. See [safety.md](safety.md).

## Making the adapter visible to Home Assistant

The integration opens a serial port, so Home Assistant has to be able to see
and open the device.

**Home Assistant OS and Supervised** pass USB devices through automatically.
Nothing to do.

**Home Assistant Container** needs the device passed into the container:

```yaml
services:
  homeassistant:
    devices:
      - /dev/serial/by-id/usb-...:/dev/serial/by-id/usb-...
```

Pass the `by-id` path, not `/dev/ttyUSB0`: the `ttyUSB` number changes when
devices are re-enumerated.

**Home Assistant Core** needs the user Home Assistant runs as to be in the
group that owns the device, usually `dialout`:

```bash
sudo usermod -aG dialout homeassistant
```

Log out and in, or restart the service, for the group to take effect.

## Finding and checking the port

```bash
# Home Assistant OS, Supervised, Container
docker exec homeassistant ls -l /dev/serial/by-id/

# Home Assistant Core
ls -l /dev/serial/by-id/
```

On Home Assistant OS, open a shell with the *Advanced SSH & Web Terminal*
add-on and turn its protection mode off, otherwise the add-on cannot reach
`docker`.

Always use the `/dev/serial/by-id/...` path. It is built from the adapter's
own identifiers and survives reboots and re-enumeration; `/dev/ttyUSB0` does
not, and a wallbox that moves to `ttyUSB1` after a reboot looks exactly like a
dead bus.

## Checking that it worked

1. The setup form in Home Assistant is the real test: it opens the port and
   reads the device before creating anything, and it can search the bus if the
   address or the parameters are not the factory ones. See
   [quickstart.md](quickstart.md).
2. For an answer outside Home Assistant, the integration ships a read-only bus
   check; see
   [quickstart.md](quickstart.md#5-prove-the-bus-before-writing-anything).
3. After setup, read the DIP-configured values back from the diagnostic
   sensors: `max_current_house`, `max_evse_current`, `downgrade_current`,
   `phase_switching_mode`. If one of them does not match what you set, the DIP
   switches are not what you think they are.

## Factory bus parameters

**(Spec)**

| Setting | Default | Changeable with the configuration tool |
|---|---|---|
| Baud rate | 57600 | 9600, 14400, 19200, 28800, 38400, 56000, 57600 |
| Frame | 8 data bits, 2 stop bits, no parity | 8E1, 8O1 |
| Device address | 50 | 10 to 50 |
| Byte and word order | big endian | no |

The MENNEKES configuration tool is a separate download from MENNEKES. This
project does not ship it, wrap it, or document its use beyond naming the
settings it changes.
