# Safety and Limitations

**This integration is not a safety device.** It is a convenience layer on top
of a Modbus interface. Nothing in it may be relied on for electrical safety,
for grid compliance or for protecting a vehicle.

## Electrical work

Installation, wiring and DIP-switch changes on the wallbox are work on a fixed
electrical installation. Have them done by a qualified electrician, following
the manufacturer's installation manual and local regulations.

## RS-485 is a single-master bus

Exactly one device may be master. If something else already drives the bus —
another energy manager, a §14a control box, a second Home Assistant instance —
then adding this integration in **Modbus master** mode produces bus
collisions, not just failed reads.

Check this *before* enabling master mode. If you are not certain, stay in
read-only mode: it never transmits a write and never sends the heartbeat.

## §14a EnWG is enforced in hardware

The grid operator's dimming signal reaches the wallbox through its own
hardware input (XG1) and the reduced current is applied **inside the wallbox**.
The wallbox limits the charging current to the smallest of:

- the energy-manager current (`0x0302`),
- the downgrade current (`0x0300`),
- the maximum EVSE current (`0x0306`),
- the maximum house-connection current (`0x0304`).

The specification states that values written over Modbus are automatically
limited by the configuration of the wallbox. This integration therefore
**cannot** circumvent a §14a downgrade and must never be presented as a way to
do so. What it does is make the state visible:
`binary_sensor.*_downgrade_active` and the downgrade-current sensor.

If your §14a control box is itself a Modbus master on the same bus, see the
previous section: read-only mode is the only correct configuration.

## The heartbeat, and what happens without it

In master mode the integration writes `0x0D00 = 0x55AA` every five seconds.
The wallbox expects it at least every ten seconds. If it stops:

- with no energy-manager fallback configured (`0x030E = 0`), the wallbox keeps
  charging with the last values it received;
- with fallback handling enabled (`0x030E = 1`), it pauses charging;
- with a fallback current (`0x030E = 6…32`), it continues at that current;
- with no vehicle connected and no fallback active, it goes into error state
  200 and will not start a session until `0x0D00`, `0x0D05` and `0x0302` are
  written again.

Configure a fallback with the MENNEKES configuration tool so a Home Assistant
restart has a defined outcome. The integration raises a repair issue when
`0x030E` is 0.

## The 0 A trap

`0x0302` is not a plain current limit:

| Written value | What the wallbox does |
|---|---|
| `0` | **No limitation** — signals its maximum current (16/32 A) |
| `0.01 … 5.99` | Invalid — signals 0 A, the documented way to pause |
| `≥ 6` | A real limit, clamped to the wallbox's own maximum |

A naive `number` entity with `min: 0` therefore requests *full load* when
dragged to zero. The number entity in this integration starts at 6 A, pausing
has its own switch, and 0 A can only be written through the
`mennekes_amtron.set_charging_current` action with `allow_unlimited: true`.

## Manufacturer rate limits

- Change the charging current no more often than every **5 seconds**. The
  integration defers a faster change and writes the newest value when the
  interval has passed.
- Pause, resume and phase switches should be at least **5 minutes** apart and
  should use hysteresis. The integration rejects faster changes with an
  explanatory error.

## Phase switching can abort a charge

Dynamic phase switching exists only on 11 kW hardware that reports phase
option 2. Even there:

- the manufacturer warns that a phase switch can abort the charging session;
- some vehicles need the charging current set to the pause value **before**
  the phase count changes. The integration does that automatically while the
  wallbox is charging;
- not every vehicle tolerates phase switching at all. Check your vehicle's
  documentation before you automate it.

## Restart

`0x0D19` may only be used while the wallbox is idle. The restart button
refuses to act in any other state.

## What is validated, and what is not

Validated: AMTRON 4You 310 11 C2, register layout v01.03.

Not validated: every other model and every older layout version. The
integration gates entities by layout version and hardware capability, so an
older device gets fewer entities rather than wrong ones — but the behaviour on
that hardware is untested.
