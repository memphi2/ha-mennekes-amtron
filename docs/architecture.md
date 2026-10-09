# Architecture

## Shape

Small, single-purpose modules; a leading `_` marks a module that only its
sibling may import. `__init__.py` is a facade that assembles runtime data and
tears it down again — no device rule lives there.

```text
registers.py          the register map, single source of truth
register_blocks.py    declared contiguous read ranges + their consistency rule
enums.py              device enumerations, member name = HA state option
decode.py             register words <-> Python values
capabilities.py       what this device's layout and hardware support
client_errors.py      bus error types and Modbus exception mapping
_client_read.py       block reads with a single-register fallback
_client_write.py      writes, write rate limiting, value validation
client.py             the pymodbus client, the bus lock, the only transport
identity.py           the read-once device facts
coordinator.py        the poll
heartbeat.py          the heartbeat task
control.py            the only write path
exceptions.py         translated Home Assistant errors
entity.py             entity base: unique id, device info, availability
config_flow.py        + _flow_serial.py, _flow_connect.py, config_schemas.py
options_flow.py       control mode, poll interval, current cap
sensor.py             + sensor_descriptions.py
binary_sensor.py      + binary_sensor_descriptions.py
number.py select.py switch.py button.py
services.py           the one action that cannot be an entity
diagnostics.py        allowlist dump + raw register image
repairs.py            + repair_issues.py
```

## Six decisions worth explaining

### One lock, because RS-485 has one master

Every transaction — poll, heartbeat and write — goes through a single
`asyncio.Lock` in `client.py`. RS-485 is a single-master bus; two overlapping
transactions are not slow, they are corrupt. Nothing outside `client.py`
touches pymodbus.

One lock alone is not enough, though, because `asyncio.Lock` is fair: a
waiter is served in arrival order. With a stalling device every wait is a
full `timeout` plus its `retries`, so a heartbeat arriving behind the poll
and a diagnostics dump waits for both of them. Measured against a device
that answers nothing, the gap between two heartbeats grew to 12 s with two
bus users and 15 s with three — past the 10 s the wallbox allows.

Bulk users therefore pass a second gate before they reach the bus, so only
one of them is ever queued in front of a heartbeat, and the heartbeat write
skips that gate. The worst case is then one transfer in flight plus its own,
independent of how many readers compete: measured 6 s, whatever the load.

### The heartbeat is not part of the poll

The wallbox needs `0x0D00 = 0x55AA` at least every ten seconds. If that write
were a step in the poll, a slow or failing poll would delay it — and a missing
heartbeat is exactly how the device reaches error state 200. So it is an
entry-owned background task on its own five-second interval, started only in
master mode and cancelled on unload.

The task *spends* the interval rather than adding it: it sleeps whatever is
left of the five seconds after the write returned. Sleeping the full interval
on top of a write that waited for the bus is the other half of how the gap
grew past the deadline. What the device actually saw is also measured — only
a delivered beat closes a gap, and a gap past the deadline raises a warning
and increments `heartbeats_late` in the diagnostics, because the wallbox
reports the resulting fault without saying why.

### One choke point for writes

`control.py` is the only module that may change device state. It exists
because `0x0302` is three different commands depending on the value, and two
of them are dangerous. Centralising it means the minimum current, the
`allow_unlimited` opt-in, the clamping, the rate limits and the diagnostics
counters are written once and cannot be forgotten by a new platform.

The charging current is also the one write that is *deferred* rather than
rejected when it comes too fast: a dragged slider should not fail. Pause,
resume and phase switches are rejected instead, because the manufacturer asks
for minutes of hysteresis there and silently queueing those would hide a bad
automation.

Two different rules meet on `0x0302`, and they are deliberately kept apart.
Every write to it has to keep five seconds from the write before; that is a
property of the register and lives in the client's rate limiter. Pausing and
resuming need five minutes between them; that is a property of the
*operation* and lives in `control.py`. Keying both on the register made the
pause switch refuse for five minutes after any slider move, and let a pause
be undone by a slider move six seconds later. A real limit written while the
pause value is active counts as a resume, whatever the caller meant by it.

### Two polling cadences

`register_blocks.py` marks each block `FAST` or `SLOW`. Ten blocks carry
values that move while a car charges; eight carry configuration that changes
when somebody reconfigures the wallbox. Reading all eighteen every few seconds
spends most of the bus re-reading a serial number.

The coordinator reads the slow blocks once a minute and carries their values
forward in the snapshot in between. That halves the bus traffic without
hiding a reconfiguration for longer than a minute, and it keeps the registers
that matter — state, signalled current, measurements — on the interval the
user chose.

Carrying a value forward is only right while the block was not due. A block
that *was* read and failed has no current value, so its keys are dropped from
the snapshot and its entities go unavailable. Keeping the last reading made a
frozen measurement indistinguishable from a live one, which is exactly the
distinction an automation acting on surplus power depends on.

### Giving up on a port, not on a candidate

The bus search tries up to 861 configurations, and each one opens the serial
port. A single failed open proves nothing: the adapter can be busy for a
moment, and a baud rate the driver cannot set fails at the open rather than
at the read. Treating the first failure as proof that the port is unusable
ended the whole search and reported "no wallbox answered" for a device that
sat later in the search space. The search therefore gives up only after five
failed opens in a row, and then says the port could not be used rather than
blaming the wiring.

### Capability gating instead of optimism

`0x0000` reports the register layout version, and registers appeared across
four of them. `capabilities.py` answers one question — may this device be
asked for this register — and the coordinator, every platform and the
diagnostics all ask it. An unsupported register is never polled, never
written and never exposed, so an older wallbox gets fewer entities rather than
a dashboard full of `unknown`.

## Data flow

```text
                 +-------------------+
   poll  ------> |                   | --- read block ---> wallbox
                 |     client.py     |
   write ------> |  (one asyncio     | --- 0x06 / 0x10 --> wallbox
                 |   lock, one       |
   heartbeat --> |   transport)      | --- 0x0D00 ------->  wallbox
                 +-------------------+

coordinator -> WallboxData (decoded snapshot) -> entities
control     -> client                          -> coordinator refresh
```

`WallboxData` is a plain snapshot: a register-key to value mapping plus the
names of blocks that failed. Entities read from it and never from the bus.

## Testing

`tests/` is flat, one `test_<module>.py` per module, with no `conftest.py`.
Home Assistant is installed from the CI matrix and used for real; only the
runtime around it is faked, in `tests/ha_fakes.py`.

`FakeModbusClient` in `tests/fakes.py` replaces the pymodbus transport, not
the integration's client: the real bus lock, the real error mapping and the
real decoding run against register words that look exactly like the device's.
It can inject connection errors, protocol errors, illegal-data-address
responses and delays, which is what makes the fallback paths and the lock
testable.

`tests/test_serial_loopback.py` goes one step further and replaces nothing. It
wires two pseudo-terminals together, runs a real pymodbus Modbus RTU server at
the far end with the wallbox's factory bus settings (57600 baud, 8N2, device
address 50) and drives it with the integration's own client. Framing, byte and
word order, the keyword-only pymodbus call signatures and both write function
codes are proven there rather than assumed. It is as close to the device as
this repository gets without hardware; everything beyond it is the on-device
verification in `docs/quickstart.md`.

That test skips itself only on a pymodbus build that ships no simulator at
all. Every Home Assistant release in the supported range resolves pymodbus
3.13.1, which ships one, so the skip no longer covers any version the
integration claims to support.

## Validation

`scripts/check_validate.py` runs the same gates as CI, in the same order:
repository, legal/provenance, quality scale, register map, pymodbus requirement, ruff,
pytest, the 99 percent coverage ratchet and `mypy --strict`.

The register-map gate also compares
[docs/modbus-registers.md](modbus-registers.md) with `registers.py` and
`register_blocks.py` — every address, span, data type, access mode, layout
version, unit, read block and enumerated value. That document is what a
reviewer holds next to the manufacturer's specification while the code is
what the wallbox sees, so the two drifting apart is the one error that would
survive every other check. `--skip-typing`
drops the last one, which is what the minimum matrix entry uses: Home
Assistant's own schema annotations differ between the ends of the supported
range, so the typing gate is run against the current release and the minimum
release is held to the runtime gates. See [SUPPORT.md](../SUPPORT.md).

## Schema validation

The config flow, the options flow and the action schema are built with
`probatio`, which is what Home Assistant installs and, since 2026.10, what it
types its own signatures against. Home Assistant still aliases the name
`voluptuous` onto it, so the old import works by accident of import order;
`scripts/check_repo.py` fails the build if one returns.

## Importing device-class enums

`switch.py`, `button.py` and `binary_sensor_descriptions.py` import their
device-class enum with a `type: ignore[attr-defined]`. In 2026.10 Home
Assistant moved those enums into each platform's `const` submodule and
re-exports them from the package with a `# noqa: F401`, which satisfies ruff
but is not an explicit re-export, so `mypy --strict` refuses to see it.

The package stays the public path: Core's own integrations import
`BinarySensorDeviceClass` from `homeassistant.components.binary_sensor` in 317
places and from the `const` submodule in none, and that submodule does not
exist at all on the oldest supported release. Importing the private module to
satisfy the type checker would therefore trade a correct import for a fragile
one. The ignores remove themselves: `--strict` reports an unused ignore as an
error the moment Core exports the names properly.
