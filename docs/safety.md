# Safety and Limitations

**This integration is not a safety device.** It is a convenience layer on top
of a Modbus interface. Nothing in it may be relied on for electrical safety,
for grid compliance or for protecting a vehicle.

## Electrical work

Installation, wiring and DIP-switch changes on the wallbox are work on a fixed
electrical installation. Have them done by a qualified electrician, following
the manufacturer's installation manual and local regulations.
[hardware.md](hardware.md) describes the Modbus side of an installation that
somebody qualified has already made safe.

## Modbus RTU uses a single master

Exactly one device may be master. If something else already drives the bus —
another energy manager, a §14a control box, a second Home Assistant instance —
then running this integration in either control mode can produce bus
collisions. Read-only mode still transmits Modbus read requests; it is not a
passive listener. It only disables register writes and the heartbeat.

Check exclusive bus ownership before setup, discovery or the smoke test.
If another master is present, stop the service or disable the direct serial
entry and use a separate supported interface or obtain the readings from the
existing master. Disabling a service-connected HA entry does not stop the app.

A LAN-to-RS-485 gateway does not make the bus multi-master. Give only the selected
HA instance or independent service access to it. Both supported LAN protocols
are unencrypted and unauthenticated: restrict the gateway to a trusted private
network and never expose its Modbus port to the Internet. The service API's token
does not protect that separate port. See [LAN setup](network-gateway.md).

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
previous section: this integration must not share that bus, even read-only.

## The heartbeat, and what happens without it

When writes are enabled, the active bus owner writes `0x0D00 = 0x55AA` every five seconds.
A failed heartbeat is retried on a one-second interval until delivery resumes.
The wallbox expects it at least every ten seconds. Longer connection outages
can still miss this deadline. If the heartbeat stops:

- with no energy-manager fallback configured (`0x030E = 0`), the wallbox keeps
  charging with the last values it received;
- with fallback handling enabled (`0x030E = 1`), it pauses charging;
- with a fallback current (`0x030E = 6…32`), it continues at that current;
- with no vehicle connected and no fallback active, it goes into error state
  200 and will not start a session until `0x0D00`, `0x0D05` and `0x0302` are
  written again.

With direct USB or LAN mode, an HA restart stops the heartbeat. With the independent
service, HA can remain offline indefinitely while the last charging state is held;
a deliberate pause remains active. Service, host, USB and LAN/gateway failures can stop the
heartbeat. Configure a fallback with the MENNEKES configuration tool so those
interruptions have a defined outcome. The integration raises a repair issue when
`0x030E` is 0.

## The 0 A trap

`0x0302` is not a plain current limit:

| Written value | What the wallbox does |
|---|---|
| `0` | **No limitation** — signals its maximum current (16/32 A) |
| `0.01 … 5.99` | Invalid — signals 0 A, the documented way to pause |
| `≥ 6` | A real limit, clamped to the wallbox's own maximum |

The **Charging current limit** number input accepts 0 A to deliberately
disable the EMS limit. It displays 0 while unlimited; 6 A or more enables a
limit again. The invalid band between 0 and 6 A is rejected. Pausing has its
own switch. Direct `mennekes_amtron_rtu.set_charging_current` action calls
still require `allow_unlimited: true` for 0 A. Unlimited mode also bypasses
the integration's extra current cap, while hardware limits remain in force.

## Manufacturer rate limits

- Change the charging current no more often than every **5 seconds**. The
  integration defers a faster change and writes the newest value when the
  interval has passed.
- MENNEKES **recommends more than 5 minutes** between pauses, resumes and phase
  switches to avoid excessive cycling. Use hysteresis in automations; the
  bundled PV pause/resume blueprint defaults to six minutes. This recommendation
  concerns charging behavior; it is not a Modbus protocol lockout.
- **Next beta, unreleased:** one shared state-change interval defaults to
  **300 seconds** and can be set from 0 to 3600 seconds. Choose 360 seconds if
  you want to exceed the manufacturer's five-minute threshold. An early
  pause, resume, phase change, release change or lock change waits in a single
  pending slot; a newer charging request replaces the older one. Stop requests
  also wait, so charging can continue until their due time. Current writes
  still observe their five-second floor, including a pause requested just after
  another current write. An early pause is queued and does not need to be
  requested again. Zero disables the longer interval, not these technical
  write guards. Fresh, consistent EVSE Idle and CP A1/A2 readings bypass the
  longer interval when no vehicle is connected. Failed reads, inconsistent
  status or layouts without CP keep the interval. Register-write guards and
  heartbeat remain active; pending waits check for unplugging every five seconds.
- Repeating the same state and recovery that preserves existing intent do not
  restart the long timer. Timing guards survive an entry reload in memory.
  A cold process start uses a fresh guard period because prior timing is
  unknown. The independent service keeps its timer and queue through an HA
  restart. Restarting the actual bus owner cancels pending commands; it does
  not replay a pending start afterward. Reading and heartbeat remain available.

Published 0.5.2b1 uses the five-second pause/resume floor and a separate
five-minute phase guard; the configurable queue is for the next beta. See
[Waiting between charging changes](user-guide.md#waiting-between-charging-changes--next-beta-unreleased).

## Phase switching can abort a charge

Before a phase request, the integration reads the charging state directly
from the wallbox. A missing, invalid or failed status reply rejects the
request before any register is written. Cached dashboard state is not used
to decide whether charging must first be paused.

Dynamic phase switching exists only on 11 kW hardware that reports phase
option 2. Even there:

- the manufacturer warns that a phase switch can abort the charging session;
- some vehicles need the charging current set to the pause value **before**
  the phase count changes. The integration does that automatically while the
  wallbox is charging;
- **next beta, unreleased:** after requesting a pause, the integration waits
  up to 30 seconds for live confirmation that charging has paused. A separate
  grace time then defaults to 5 seconds and is configurable from 0 to 30
  seconds. The state is checked again before sending the phase command. A
  failed confirmation or timeout leaves the phase request unsent and retains
  the pause. Zero grace time does not disable confirmation or the shared
  state-change interval. An idle wallbox with no vehicle does not need pause
  confirmation or the extra wait;
- the integration leaves charging paused after this sequence. Resume
  explicitly after the waiting interval;
- not every vehicle tolerates phase switching at all. Check your vehicle's
  documentation before you automate it.

## Error recovery

With writes enabled, a confirmed error 200 triggers automatic recovery once the
heartbeat is healthy. It respects a pause, device lock and explicitly disabled
charging release while reasserting the EMS commands. A pause stays at the
pause value, unlimited mode stays at 0 A, and a normal limit is kept or lowered
to the remembered limit. Manual recovery follows the same rules and cannot
resume charging. At most three automatic attempts are made while the fault
persists; clearing a repair requires a confirmed error-free state.

Recovery checks the current-write guard before sending its
heartbeat/release/current sequence. In the next beta, reasserting the same
charging intent is exempt from the longer state-change interval; it must not
turn a heartbeat fault into a five-minute recovery lockout. Recovery does not
authorize a queued start to run early. A transport failure during the sequence
can still leave a partial operation; it is reported as a failed recovery.

## Restart

`0x0D19` may only be used while the wallbox is idle. The restart button
reads the current state immediately before the command. It refuses to act
in any other state or when that fresh read fails or returns an unknown value.

## What is validated, and what is not

Automated tests exercise real Home Assistant with simulated register data
and a virtual Modbus RTU server, using the Amtron 4You 310 11 C2 on layout
v01.03 as the development target. The owner reported successful operation of
the released integration on that model on 2026-11-03. Firmware and a detailed
test checklist were not supplied; this does not validate later development
changes. See the [hardware-test scope and model matrix](compatibility.md).
The [software compatibility audit](ha-2026-11-validation.md) records tested
software versions and remaining checks.

Entities are filtered by reported layout version and hardware capability.
This reduces unsupported operations but does not establish correct behavior
on every firmware or vehicle.

The configured wallbox current limits restrict the wallbox, not the combined
load of other household consumers. PV blueprints and example automations are
not overload protection for the house connection. Loss of an external surplus
sensor does not stop the integration's heartbeat or automatically pause charging.
