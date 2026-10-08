# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Changed

- **Minimum Home Assistant is now `2026.9.0`, and `2026.10.0` is the validated
  current release.** Home Assistant replaced voluptuous with `probatio` in
  2026.9 and, from 2026.10, types its own config-flow and action signatures
  against it. The integration now imports `probatio` directly, as Home
  Assistant Core itself does, instead of relying on the compatibility alias
  that makes the old import work only because Home Assistant is imported
  first. Releases older than 2026.9 do not ship `probatio` and are no longer
  supported.
- The repair flow is typed with `RepairsFlowResult`, which 2026.10 narrowed to
  its own flow context.
- pymodbus minimum raised to `3.13.1`, the version every Home Assistant
  release in the supported range resolves. The serial loopback test no longer
  skips itself on older pymodbus builds, so it runs on every matrix entry.
- The early-warning CI job resolves the newest Home Assistant pre-release
  instead of pinning one, so it can no longer end up testing a beta that a
  later release has already superseded.
- The typing gate runs against the current Home Assistant only, because no
  single source file can satisfy `mypy --strict` against both ends of the
  supported range. The minimum entry runs every other gate, including the full
  test suite. `scripts/check_validate.py --skip-typing` does the same locally.

### Added

- A repository gate that fails the build if a voluptuous import returns, since
  Home Assistant's alias would otherwise let one pass unnoticed.

### Fixed

- Dropped the unused `voluptuous-serialize` development dependency. It was the
  only reason the real voluptuous was installed in the validation environment,
  which masked how the integration actually resolves its schema library.

## 0.1.0

Initial release.

A local Home Assistant integration for MENNEKES AMTRON wallboxes over Modbus
RTU, built against the manufacturer's Modbus RTU specification revision 2.5
(register layout v01.03) and developed for an AMTRON 4You 310 11 C2.

> **Not yet verified on hardware.** Every value, rule and limit in this release
> comes from the specification and is exercised against a real Modbus RTU
> server over a virtual serial link. None of it has been measured on a real
> wallbox. Start in read-only mode and work through the verification steps in
> `docs/quickstart.md`.

### Setup

- Config flow that opens the serial port and reads the Modbus layout version
  and serial number before an entry exists, so a wrong port, address or bus
  parameter fails during setup instead of producing an empty device.
- The form **searches the bus** when the values entered do not work: every
  documented device address at those bus parameters, or every documented
  combination of address, baud rate and frame. It runs as a progress step with
  an estimate, tries the user's own values first, and only reads.
- Five fields, not seven: the device address is a number box, and the frame is
  one choice of the three the specification documents, so impossible
  combinations like 8N1 are unreachable.
- Reconfigure flow for the bus parameters, pre-filled from the entry. A failed
  connection test keeps what was typed.
- Options for the control mode, the polling interval and a charging-current
  cap.
- A new entry is **read-only**: it polls and creates sensors, but never writes
  and sends no heartbeat. Control entities appear only after switching the
  control mode to Modbus master.

### The device

- 36 sensors, 9 binary sensors, 1 number, 3 switches, 2 selects and 2 buttons,
  all translated to English and German including every enumerated state.
- Capability gating by register layout version (`v01.00`–`v01.03`) and by the
  hardware phase option: a register the firmware does not have produces no
  entity, rather than an entity stuck at `unknown`.
- 18 declared contiguous read blocks with a single-register fallback when a
  device rejects a range read. Eight of them carry configuration and are read
  once a minute instead of every poll, which keeps the bus for the ten that
  move.
- `sensor.*_energy_total` feeds the Energy dashboard. The manufacturer marks
  that register as not usable for billing, and the entity says so.

### Safe control

- One write path. `0x0302` means three different things depending on the
  value: `0` removes the limit and makes the wallbox signal its **maximum**,
  `0.01`–`5.99` is the documented pause, `≥ 6` is a real limit. The number
  entity never reaches the first two; pausing has its own switch, and "no
  limitation" needs an explicit opt-in on the action.
- A dedicated heartbeat task writes `0x0D00 = 0x55AA` every five seconds in
  master mode, decoupled from the data poll, because a slow poll must never be
  able to starve it.
- One `asyncio` lock for every transaction, because RS-485 is a single-master
  bus.
- The manufacturer's rate limits are enforced: a charging-current change
  faster than five seconds is deferred and the newest value written; a pause,
  resume or phase switch faster than five minutes is refused with an
  explanatory error.
- A phase switch pauses a running charge first, as the manufacturer's own
  sequence does. Restarting the wallbox is refused unless it is idle.
- The §14a EnWG downgrade is enforced in the wallbox hardware. The integration
  makes it visible through `binary_sensor.*_downgrade_active` and the
  downgrade-current sensor, and never tries to work around it.

### Automations

- Four blueprints, installed into `config/blueprints/automation/` when the
  integration is set up: charge from PV surplus, pause and resume on PV
  surplus with hysteresis, recover from a lost heartbeat, and notify on a
  grid-operator downgrade. The installer never replaces a blueprint that was
  edited.
- `mennekes_amtron.set_charging_current` with an `allow_unlimited` opt-in,
  for the one argument that cannot be offered safely as an entity.

### Diagnosis

- Diagnostics built from an allowlist: no serial port path, a hashed serial
  number, and the full raw register image, which is what makes a support
  report decidable.
- Four repair issues: a lost energy-manager heartbeat (fixable, runs the
  documented recovery sequence), a missing fallback configuration, an older
  register layout, and a refused write.
- A read-only bus check that ships with the integration, so it runs on the
  machine the USB adapter is plugged into, with Home Assistant's own Python.
  It can search for the device address and the baud rate.

### Quality

- All 52 official Home Assistant quality-scale rules answered: 43 `done`,
  9 `exempt`, none open.
- 338 tests and a 99 percent coverage ratchet. The suite includes an
  end-to-end test over a virtual serial link against a real Modbus RTU server,
  and a smoke test that boots Home Assistant, drives the real config and
  options flows, and runs the blueprints as real automations.
- `mypy --strict` over every module and every script.
- `ruff` with 38 rulesets, including the bandit security rules.
- Validation gates for the repository, legal provenance, the quality scale,
  the register map and the pymodbus requirement, run identically locally and
  in CI against the minimum and current Home Assistant, plus an early-warning
  job against the next pre-release.
