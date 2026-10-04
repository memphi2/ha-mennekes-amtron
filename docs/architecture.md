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

## Four decisions worth explaining

### One lock, because RS-485 has one master

Every transaction — poll, heartbeat and write — goes through a single
`asyncio.Lock` in `client.py`. RS-485 is a single-master bus; two overlapping
transactions are not slow, they are corrupt. Nothing outside `client.py`
touches pymodbus.

### The heartbeat is not part of the poll

The wallbox needs `0x0D00 = 0x55AA` at least every ten seconds. If that write
were a step in the poll, a slow or failing poll would delay it — and a missing
heartbeat is exactly how the device reaches error state 200. So it is an
entry-owned background task on its own five-second interval, started only in
master mode and cancelled on unload.

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

## Validation

`scripts/check_validate.py` runs the same gates as CI, in the same order:
repository, legal/provenance, quality scale, register map, pymodbus pin, ruff,
pytest, the 95 percent coverage ratchet and `mypy --strict`.
