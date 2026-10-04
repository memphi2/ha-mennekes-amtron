# Home Assistant Quality Scale

Target: Platinum.

Current status: Platinum. Open rules: none — all 52 official rules are
answered, 43 as `done` and 9 as `exempt`, each exemption with a reason.

`custom_components/mennekes_amtron/quality_scale.yaml` is the
machine-readable status. `scripts/check_quality_scale.py` fails the build when
it drifts from this document, when a rule is missing or unknown, and when any
rule goes back to `todo` without being documented as a blocker here.

## What the tiers cost, and what was done

**Bronze** wants a config flow that is actually tested against the device. The
flow opens the serial port and reads the Modbus layout version and the serial
number before an entry exists, so a wrong port, baud rate or device address
fails during setup instead of producing an empty device.

**Silver** wants the integration to behave when the device does not. A failed
connect or identity read raises `ConfigEntryNotReady`; entities go unavailable
when the poll fails *and* when their own register is missing from the
snapshot; the coordinator logs a loss and a recovery exactly once per state
change; command platforms set `PARALLEL_UPDATES` to 1 because RS-485 carries
one transaction at a time.

**Gold** wants the integration to be usable and documented. Every entity has a
translated name, every enumerated state has a translated value in English and
German, every entity has an icon, every error the user can trigger is a
translated exception, and four repair issues explain the four ways this device
gets stuck. `scripts/check_register_map.py` fails the build if any of that
drifts from the register map.

**Platinum** wants a fully typed, fully asynchronous integration.
`scripts/check_typing.py` runs `mypy --strict` over every module and every
script, and pymodbus' async serial client is used throughout.

## Exempt rules, and why

- `brands`: the Home Assistant Brands repository serves core integrations.
  This one is installed through HACS, which serves the project-owned brand
  assets from the repository. `docs/legal.md` pins their hash.
- `entity-event-setup`: all entities derive from the coordinator. There is no
  device-side event stream to subscribe to in an entity lifecycle callback.
- `integration-owner`: the repository deliberately ships no personal metadata,
  so `manifest.json` keeps an empty `codeowners` list. The maintenance scope
  is documented in `SUPPORT.md` instead.
- `reauthentication-flow`: a serial bus has no credentials to re-enter.
- `discovery` and `discovery-update-info`: a USB RS-485 adapter cannot be
  identified as this wallbox. The config flow offers a port list, which is
  honest, instead of claiming a discovery it cannot perform.
- `dynamic-devices` and `stale-devices`: one config entry models exactly one
  wallbox.
- `inject-websession`: the integration speaks serial Modbus RTU and makes no
  HTTP request.

## Rules this integration earns that a push integration cannot

- `appropriate-polling`: the device has no push channel, so polling is the
  correct model. The interval is five seconds by default and configurable, the
  reads are blocked into eighteen contiguous ranges rather than fifty single
  reads, and the heartbeat is deliberately kept out of the poll.
- `test-before-configure`: the config flow reads the device before an entry is
  created.
- `log-when-unavailable`: the serial connection is held open, so a loss and a
  recovery are each logged once.

## Coverage

The ratchet is 99 percent, above the Platinum target of 95. The suite leaves
no statement of the integration uncovered; what remains are three partial
branches in loops that only exit on cancellation.

Coverage is not the claim, though. The tests that matter are the ones that
pin the device's dangerous semantics: that `0x0302` can never be written with
0 A without an explicit opt-in, that the invalid 0.01-5.99 A band is refused
even with it, that the heartbeat keeps running while the poll fails, that the
bus lock serializes every transaction, and that the recovery sequence writes
its three registers in the documented order.

## What is still open

Nothing in the quality scale. What is open is the device: no value in this
integration has been measured against real hardware yet. The verification
steps in [quickstart.md](quickstart.md) and [safety.md](safety.md) are what
close that gap, and until they are done the repository stays private.

The CodeQL workflow and branch protection are in the same position: both need
a public repository, and both switch themselves on when it is published. See
[repository-settings.md](repository-settings.md).
