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
transport.py          typed transport and response interfaces
write_state.py        shared write guards retained across entry reloads
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
services.py           explicit current action and service registration
recorder_profile.py   read-only Recorder suggestions using registered IDs
diagnostics.py        allowlist dump + raw register image
repairs.py            + repair_issues.py
```

## Six decisions worth explaining

### One lock, because Modbus RTU has one master

Poll snapshots carry an internal current-write generation. A current write
with a successful or uncertain outcome advances that generation under the
bus lock. A poll that started earlier cannot overwrite the controller's
confirmed current or pause state. The generation survives entry reloads,
is excluded from snapshot equality, and is never an entity attribute or
Recorder value. A later poll in the current generation can confirm the state.

Every transaction — poll, heartbeat and write — goes through a single
`asyncio.Lock` in `client.py`. Modbus RTU is a single-master protocol; two overlapping
transactions are not slow, they are corrupt. Nothing outside `client.py`
touches pymodbus.

One lock alone is not enough, because `asyncio.Lock` is fair: a waiter is
served in arrival order. A heartbeat must not queue behind every pending
poll, diagnostic request and user action.

Bulk users therefore pass a second gate before they reach the bus, so only
one of them is ever queued in front of a heartbeat, and the heartbeat write
skips that gate. Its wait depends on the current transfer, including that
read's bounded retries, rather than the number of readers in the queue.
Fallback block reads release the bus between individual register ranges.

The pymodbus transport has automatic retries disabled: a write with a lost
reply might already have taken effect and must not be resent inside the
five-second floor. Only reads retry, explicitly and within the configured
budget. Cancellation wrapped by pymodbus is restored as task cancellation
on both paths. `tests/test_serial_failures.py` verifies actual RTU bytes,
missing replies, cancellation and exclusive-port release.

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

After a failed heartbeat, its task uses a one-second retry interval, still
subtracting the time spent on the failed attempt. Once delivery succeeds it
returns to five seconds. This avoids spending the entire ten-second deadline
on one brief disconnect. Only the heartbeat's fixed, idempotent value receives
this treatment; control writes retain their existing intervals and are never
automatically replayed. Repeated failures produce one failure event until
recovery, rather than growing the event history on every retry.

### One choke point for writes

`control.py` is the only module that may change device state. It exists
because `0x0302` is three different commands depending on the value, and two
of them are dangerous. Centralising it means the minimum current, the
`allow_unlimited` opt-in, the clamping, the rate limits and the diagnostics
counters are written once and cannot be forgotten by a new platform.

Current commands retain the newest pending value when requested inside the
five-second register interval. Pause/resume commands are instead rejected
with the short remaining wait, so an old deferred setpoint cannot silently
undo a later pause. The manufacturer recommends longer anti-cycling hysteresis;
that belongs in automation policy, and the bundled PV blueprint defaults to
six minutes. Manual pause/resume no longer has a five-minute lockout.

Phase requests retain a separate five-minute interval. Both guards survive
entry reloads, including uncertain write outcomes. A cold start applies five
seconds to current and pause/resume, and five minutes to phase requests.

### Three polling cadences, and one of them is about the database

`register_blocks.py` marks each block `FAST`, `ACTIVE` or `SLOW`.

`SLOW` is about the bus. Eight blocks carry configuration that changes when
somebody reconfigures the wallbox, not while it charges — the serial number,
the article number, the DIP-configured limits. The coordinator reads them once
a minute and carries their values forward in between, which halves the bus
traffic without hiding a reconfiguration for longer than a minute.

`ACTIVE` contains measurements, signalled current, session values and detected
vehicle phases. These blocks have a separate configurable cadence, defaulting
to 30 seconds. While unplugged, the cadence is at least 60 seconds. Plugged-in
vehicles no longer generate five-second voltage history merely by remaining
connected overnight.

The coordinator reads `FAST` blocks first. An EVSE state transition forces the
active blocks and lifetime counters to refresh in that same poll, so starting
and stopping a charge does not leave a stale power reading until the next
measurement interval. Failed block names remain in diagnostics until the
block is actually read successfully again.

The [Recorder replay](recorder.md#measured-load) uses real Home Assistant
entities and SQLite with synthetic register data. It counts stored state rows
rather than estimating writes from the number of bus reads. It does not model
24 hours of statistics, retention or physical-wallbox behavior.

The rounding is the other half. `suggested_display_precision` only tells the
frontend how to render a state; what Home Assistant records and builds
statistics from is the native value. Decoding a float32 carries the conversion
artefact along, so 230.1 V arrives as `230.10000610351562` and is stored that
way — and the device's last bits move whenever its meter recomputes, which on
its own is enough to write a row on every poll. `sensor.py` therefore rounds
every numeric state to the precision the entity says it will display.

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

`switch.py`, `button.py` and `binary_sensor_descriptions.py` keep importing
platform device classes through the public package at runtime. Home Assistant
2026.9 has no separate `const` module for these enums; 2026.10 moves them there
without an explicit typed re-export, and 2026.11 fixes the re-export.

Under `TYPE_CHECKING`, the imports point to the defining `const` modules. This
lets both the 2026.10 and 2026.11 typing gates check the actual enum without
version-dependent ignore comments, while retaining the 2026.9 runtime path.
