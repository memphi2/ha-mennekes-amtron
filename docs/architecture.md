# Architecture

## Independent service

The `device/` package has no Home Assistant imports. It contains the shared
register map, USB/LAN transport, poll cadence, heartbeat and control rules.
`control.py` at integration level adapts its errors into HA translations.
The same source is packaged as `amtron_device` for the independent service;
there is no copied driver implementation to keep in sync.

Integration and service versions are independent. The HTTP client checks API 2
and a separate minimum service version (currently 0.6.1), not equality with the
integration release. The minimum supplies service-owned expiry and cancellation
of timed charging commands even during HA outages. Old API 2 snapshots without
`service_version` identify the 0.5.0 baseline and fail this newer requirement.
Current service snapshots report their own version.
An integration-only release reuses the compatible published app, so updating
HA does not restart the service or interrupt its heartbeat. Service changes
have their own version and publication decision; see [publication](service-releasing.md).

```mermaid
flowchart LR
    HA[Home Assistant integration] <-->|Authenticated HTTP| Service[Modbus service]
    Service <-->|USB or LAN gateway / RS-485| Wallbox[Mennekes Amtron]
```

`remote.py` maps service snapshots and named commands onto the existing HA
entities. API reconnects are read-only; HA restore values cannot overwrite
service intent. The service serializes commands independently of client lifetime
and keeps heartbeat and polling active without HA. Its state file only changes
with durable control intent or a completed charging summary, not ordinary meter
progress. Named commands retain device guards; the API has no raw
register-write endpoint. Current telemetry retains its existing cadence and
rounding; heartbeat counters remain diagnostics rather than Recorder attributes.

Percentage-target updates reserve a bounded, short-lived service token before
submission. After a lost reply, resolution either confirms completion or prevents
an unstarted request from executing later. The HA observer keeps evidence against
both the old and requested goal until the outcome is known. A service-owned goal
revision prevents buffered reports from stopping a subsequently replaced target.
At most 64 reservations are retained in RAM, with a five-minute lifetime; tokens
do not survive a service restart and are not Recorder attributes. A restarted
service or an unknown storage outcome is never inferred to be a successful save.
Older API 2 services remain usable for other controls; setting a percentage goal
requires this capability and otherwise reports that the app needs an update.

Explicit target saves finish before propagating cancellation, including time
spent waiting for a storage lock. The corresponding in-memory model and command
invalidation must finish with that save. This keeps a cancelled request from
leaving a different target active until the next restart.

An atomic replacement can succeed before its directory synchronization fails.
The service records that phase directly; the HA store checks the resulting file.
A confirmed replacement keeps the visible candidate active while reporting
unconfirmed durability, and the write cache cannot suppress a retry. If the
resulting file cannot be read, target controllers block starts and request a
pause until a successful save resolves the uncertainty; departure planning also
stays blocked. This process-local protection cannot prove what a damaged
filesystem will retain after a crash or power loss.

Direct USB and LAN modes remain available without the service. A service
entry talks only to the service API; it never opens the USB or gateway Modbus
connection or starts a local heartbeat/recovery task.
All entries preserve the domain and entity unique IDs. See
[setup and migration](modbus-service.md) for the one-owner handover.

In service mode, the authenticated service snapshot is authoritative for write
permission. HA has no separate permission selector. Control platforms remain
loaded, preserving entity IDs, while entity availability and command guards
follow the service's read-only flag and current connection/readiness state.
Permission transitions send no charging commands and do not replay HA restore
data. Direct USB and LAN entries retain their own explicit control mode.

The shared transport supports local USB/serial, transparent RTU frames over TCP
and Modbus TCP via a translating TCP-to-RTU gateway. Both the service and direct
HA mode use the same driver and register model. LAN configuration stores protocol,
host, TCP port and device address; serial baud/frame are configured on the
gateway. HA-to-service HTTP remains a separate connection. See
[LAN setup](network-gateway.md) and the [security boundary](../SECURITY.md#threat-model).

Optional Waveshare TCP login authenticates each new transport socket before
pymodbus can send application traffic. Its adapter password is independent of
the service's HTTP token. The explicit adapter-configuration path is separate
from normal connection startup: it inspects one endpoint, validates the model
and native parameter profile, applies passive request/reply settings and the
selected connection fields, verifies readback and
retains a reversible receipt until caller validation/persistence completes.
Cancellation runs a bounded, shielded restoration. A failed restoration leaves
the old owner stopped. See [adapter setup and limitations](lan-adapters.md).

Since **0.6.0b1**, an administrator connection page runs on a separate
internal Ingress listener. The updated page requires stable Supervisor
2026.10.4+; earlier app releases required 2026.11.0+. It enumerates only locally
mapped serial adapters and saves connection fields through the
Supervisor API for this app. It does not expose the service token to the browser.
Ordinary connection saves take effect after an app restart. Explicit LAN-adapter
setup stops the old connection before changing the adapter; local validation
errors leave the existing connection running. The user restarts the app after
a successful adapter change to resume Modbus with the saved settings.
Other app settings remain in the native Configuration tab.

The [Supervisor 2026.10.4 ingress implementation](https://github.com/home-assistant/supervisor/blob/2026.10.4/supervisor/api/ingress.py)
enforces `panel_admin`, removes incoming user and group headers regardless of
case, and sets the groups from the authenticated user. Earlier Supervisor
releases do not provide all of these checks and headers together.

An app with missing connection fields starts only its setup interface and health
endpoint, with no Modbus owner, heartbeat or discovery. A configured but missing
adapter still follows the normal reconnect path. Standalone configuration remains
strict and has no Ingress setup mode. See [connection setup](upgrading.md#app-connection-page).

The driver binds a readable serial number to the client. Reconnection
invalidates that verification; the next transaction checks it again under the
bus lock. Control writes also read the serial to catch a downstream gateway
change without a TCP disconnect. A mismatch blocks writes and preserves saved
intent. Ordinary heartbeat delivery adds no serial-number read or Recorder
update. Devices without a readable serial retain their documented endpoint
binding limitation.

The ordinary request queue stays owned while a priority heartbeat can pass
between identity verification and the following data read. Reacquiring the bus
also rechecks the client and connection generation; changed or closed transports
cannot supply the result. Failed measurement blocks have individual bounded
retries, without accelerating the remaining measurement groups or adding
per-retry Recorder attributes.

`tests/test_service_process.py` runs the server in a separate process over a
virtual serial Modbus bus while a real HA instance stops and starts. It checks
continued heartbeat delivery, unchanged pause and absence of replayed commands.
The other service tests cover migration identity, API validation, bounded queues,
recovery and atomic state persistence. These simulations do not validate the
physical wallbox's firmware or the Supervisor/container runtime. CI separately
builds the standalone wheel and app container and checks their entrypoints.

## Shape

Small, single-purpose modules; a leading `_` marks a module that only its
sibling may import. `__init__.py` is a facade that assembles runtime data and
tears it down again — no device rule lives there.

```text
device/registers.py          the register map, single source of truth
device/register_blocks.py    declared contiguous read ranges + their consistency rule
device/enums.py              device enumerations, member name = HA state option
device/decode.py             register words <-> Python values
device/capabilities.py       what this device's layout and hardware support
device/client_errors.py      bus error types and Modbus exception mapping
device/_client_read.py       block reads with a single-register fallback
device/_client_write.py      writes, write rate limiting, value validation
device/client.py             the pymodbus client, the bus lock, the only transport
device/transport.py          typed transport and response interfaces
device/write_state.py        shared write guards retained across entry reloads
device/identity.py           the read-once device facts
coordinator.py        the poll
device/heartbeat.py          the heartbeat task
recovery.py           bounded recovery of a confirmed heartbeat fault
device/control.py     the only write path
device/poller.py      shared cadence-aware polling
device/service_*.py   service runtime, API, options and intent storage
device/server.py      standalone process entrypoint
remote.py             HA service client
control.py            HA command error adapter
exceptions.py         translated Home Assistant errors
entity.py             entity base: unique id, device info, availability
config_flow.py        + _flow_serial.py, _flow_connect.py, config_schemas.py
options_flow.py       options menu and section routing
_options_*.py         independent draft, vehicle, departure and estimate steps
vehicle_profiles.py  bounded profiles and effective vehicle options
vehicle_sources/     optional provider capabilities and selection compatibility
vehicle_sources/bavariandata/  local telemetry, timer and charging-history adapters
timed_goal_observer.py  departure plan from generic HA sources or a provider
charge_loss_observer.py  bounded matching and durable loss aggregate
sensor.py             + sensor_descriptions.py
binary_sensor.py      + binary_sensor_descriptions.py
number.py select.py switch.py button.py
services.py           explicit current action and service registration
recorder_profile.py   read-only Recorder suggestions using registered IDs
diagnostics.py        allowlist dump + raw register image
repairs.py            + repair_issues.py
```

## Design decisions

### Vehicle integrations provide data through narrow capabilities

Generic Home Assistant entities remain available for SoC, usable capacity,
dated departure, price and last-charge energy. Manual departure and calendar
selection work without a manufacturer adapter. The optional `vehicle_sources`
package translates supported vehicle telemetry, departure timers and charging
history into small provider-independent contracts. Its metadata also supplies
the configuration's device filter. BavarianData is the first adapter; this does
not claim support for other manufacturers' proprietary timer or history formats.

The SoC observer consumes bounded telemetry updates. The scheduling observer
requests usable capacity and a next departure instant. The loss observer
requests a bounded match and subscribes to local history changes. VIN parsing,
entity descriptors, provider event names and the local public history service
remain inside the adapter. Neither observer calls a vehicle cloud API or
interprets manufacturer identities, and the Modbus control layer has no
vehicle-provider dependency.

BavarianData telemetry is resolved by the selected device and its registered
`VIN_descriptor` identities, with matching descriptor attributes. Charge level
uses `vehicle.drivetrain.batteryManagement.header`, the optional maximum uses
`vehicle.powertrain.electric.battery.stateOfCharge.target`, and usable capacity
uses `vehicle.drivetrain.batteryManagement.maxEnergy`. Installed battery size
and formatted battery-health estimates are not capacity fallbacks. Only already
enabled local entities are read; missing or invalid values remain unavailable.

Profiles hold a stable ID, name, provider/device selection and independent
feature settings. One explicitly selected profile supplies effective vehicle
options; wallbox and cost settings remain shared. Creating or selecting a
profile does not enable optional features. No automatic vehicle recognition is
performed. Profile changes invalidate the previous target and trip, while loss
aggregates are retained separately per profile. Provider adapters bind the
vehicle identity and reject a device rebound to another car.

Without profile configuration, existing flat options retain their behavior,
including independently selected departure and history vehicles. Explicit
profile setup preserves those settings as a manual profile instead of guessing
that two old selections describe the same car. Provider/device keys still take
precedence over older keys, including an explicitly disabled provider.

Options edits are held in a section-local draft until the final form is
submitted. Completing it merges only that section's keys into the latest
options and rejects a concurrent edit to the same section. Vehicle sections
also retain the active profile identity, so an open form cannot modify another
car after a profile switch. Returning to the main menu discards an unfinished
draft.
Departure activation is independent of merely configuring an SoC sensor;
disabling it clears the trip and cancels only its owned queued commands.

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
heartbeat is exactly how the device reaches error state 200. The heartbeat
therefore has its own background task and five-second interval.

With a **direct USB or LAN connection**, HA starts that task only in master mode,
before the first full data poll, and cancels it when the entry unloads. A separate
deadline timer marks its diagnostic health unhealthy even while a transaction
is blocked. The Heartbeat problem sensor subscribes only to health transitions
and remains available when register polling fails. It never exposes per-beat
timestamps or counters to Recorder. Timer and listener lifetimes follow the entry.

With the **independent service**, initialization first reads the identity, loads
saved intent and completes the first data poll. The service then starts its own
heartbeat task if writes are enabled. HA reads the reported health through the
API; unloading its integration entry does not stop the service's task. Stopping
the service or its host interrupts that heartbeat.

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
this treatment; control writes retain their existing intervals. Repeated failures produce one failure event until
recovery, rather than growing the event history on every retry.

The recovery worker subscribes to heartbeat health and coordinator changes.
It runs for a confirmed error 200 and healthy heartbeat delivery in master
mode, including when charging is paused, locked or not released. Pause and
release intent are retained in WriteState across reloads and in the switches'
extra restore data across HA restarts, without Recorder attributes. Fault
readback cannot overwrite that intent. Release commands and recovery share
the current-command lock. Recovery reads the error and current again before
writing heartbeat, the intended release and the preserved current setting.
A saved pause takes precedence over reset current readback; its saved resume
limit is retained. Recovery never writes the device-lock register.

The vendor specifies rewriting the three EMS registers after a heartbeat
fault. Its >=6 A startup example is not used as permission to resume a paused
charge. Recovery reasserts the pause value or zero directly, with no temporary
charging limit or deferred restore command. Tests model fault clearance once
all three commands arrive and verify no transaction permits charging during
a paused recovery. Firmware acceptance of fault clearance while paused still
requires hardware validation; a persistent fault remains visible and never
causes an automatic escalation to a running current.
At most three attempts are made per persistent fault, 30 seconds apart;
current-write cooldowns delay attempts without consuming the budget. A
confirmed error-free poll resets the budget. Entry unload cancels the task,
retry timer and both listeners. Healthy operation adds no polling or states.

### One choke point for writes

`control.py` is the only module that may change device state. It exists
because `0x0302` is three different commands depending on the value, and two
of them are dangerous. Centralising it means the minimum current, the
`allow_unlimited` opt-in, the clamping, the rate limits and the diagnostics
counters are written once and cannot be forgotten by a new platform.

**Since 0.5.2b2:** current writes retain their five-second register
floor. A separate `state_change_interval_seconds` option defaults to 300 and
accepts 0–3600 seconds; zero disables the longer guard. The direct integration
owns this option in direct mode, while the service configuration owns it in
service mode. Remote HA commands use the service's policy.

One pending-command slot and worker coordinate both short current-write delays
and longer state-change waits. A newer command supersedes older pending work.
Pause/resume, phase changes, both release directions and lock/unlock share the
transition clock; the current-write floor also remains. A stop request replaces
an older pending start and can itself wait in that slot. A phase command's pause
and phase write form one operation. Before sending the phase write, live status
checks must confirm that charging has paused within a bounded 30-second wait.
For a connected vehicle, layouts v01.02 and newer require EVSE state 2 and CP
state B1; v01.01 uses EVSE state 2 because it has no CP-state register.
The separate `phase_switch_grace_seconds` setting defaults to 5 and accepts
0–30 seconds. After this additional wait, status is rechecked before the phase
write. A read failure or timeout aborts the phase command and retains the pause.
Zero grace time skips only the additional delay. The operation leaves charging
paused afterward. With no vehicle and an idle EVSE, confirmation and grace time
are unnecessary. Fresh EVSE Idle and CP A1/A2 reads also exempt commands from
the shared transition guard. Missing CP support or unreadable/inconsistent
status never grants that exception. The transition timestamp is still retained,
so reconnecting a vehicle restores the guard. During pending transition and
grace waits, the worker checks for unplugging every five seconds without
changing the published due timestamp; register-write floors remain in force.
Idempotent commands do not restart the timer. Recovery reasserts existing intent
without consuming a new transition or waiting for the longer interval.

The worker releases locks while waiting and revalidates the operation before
execution. It exposes a stable pending action and a UTC due timestamp during
timed waits. While awaiting vehicle pause confirmation, the phase action remains
visible with no due timestamp; confirmation sets a fixed timestamp for the end
of the grace period. No per-second state or timestamp writes are required.
Register readback remains
separate from requested state. Shutdown cancels pending work, so a new owner
never silently replays a queued start. The running service and its queue survive
an HA restart. Entry reloads retain timing in memory; cold process starts use
a conservative fresh guard because previous timing is unknown.

### Three polling cadences, and one of them is about the database

`register_blocks.py` marks each block `FAST`, `ACTIVE` or `SLOW`.

`SLOW` reduces bus traffic for identity/configuration blocks and less frequently
sampled values such as temperature, total energy and the session counter. These
values are not all static. They become due after 60 seconds and are read on the
next poll; their previous values are retained between due polls. Current limits,
including the reported maximum EVSE current, belong to `FAST` and are read on
every poll.

`ACTIVE` contains measurements, signalled current, session values and detected
vehicle phases. These blocks have a separate configurable cadence, defaulting
to 30 seconds. While the EVSE reports idle, the cadence is at least 60 seconds. Plugged-in
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
lets the latest-stable typing gate check the actual enum without
version-dependent ignore comments. The minimum HA 2026.9 validation runs the
runtime tests with `--skip-typing`, retaining the public-package import path.
