# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.1

### Fixed

- The release carried a single archive with no wrapping directory. That is
  what HACS needs, because it extracts the archive *into*
  `custom_components/mennekes_amtron/`, but unpacking it by hand scattered
  fifty files instead of installing an integration. The release now carries
  two archives: `ha-mennekes-amtron.zip` for HACS, unchanged in shape, and
  `ha-mennekes-amtron-manual.zip`, which carries the full
  `custom_components/mennekes_amtron/` path and unpacks straight onto a
  configuration directory.
- The archive no longer installs `PRIVACY.md`, `SECURITY.md`, `legal.md` and
  `safety.md` into the user's `custom_components` directory. Only the licence
  and the notice travel with the integration, as Apache-2.0 asks; the rest
  stays in the repository, where their links resolve.

- The config entry title no longer carries the wallbox serial number. The
  title becomes the device name, and Home Assistant prefixes the device name
  to every entity name and every entity id, so entities were called
  `sensor.mennekes_amtron_1313201205_abc123456789_voltage_l1` and the serial
  number ended up in entity ids, history and any shared dashboard. The serial
  stays where it belongs: as the entry's unique id and on the device page.
- The config flow keeps what was typed when a connection test fails, instead
  of resetting the form to the factory defaults.
- Reconfiguring an entry starts from that entry's bus parameters instead of
  the factory defaults.

### Changed

- `0x030E` is no longer reported as an ampere value for every reading. The
  register holds a mode for 0 and 1 and a charging current only for 6 to 32,
  so a new diagnostic sensor reports the behaviour (disabled, pauses on
  heartbeat timeout, charges with the fallback current) and the current sensor
  reports a value only when the register really holds one.

### Added

- A smoke test that boots a real Home Assistant, drives the real config and
  options flows against a real Modbus RTU server and checks the entity
  registry, the device registry, the repair issues and the write path. The
  three defects above were invisible to the rest of the suite because it
  replaces Home Assistant's runtime with fakes.

## 0.1.0

First release.

### Added

- Config flow with a real connection test: the serial port is opened and the
  Modbus layout version and serial number are read before an entry exists.
- Options flow for the control mode (read-only or Modbus master), the polling
  interval and a charging-current cap.
- Reconfigure flow for the bus parameters.
- Polling coordinator over 18 declared register blocks, with a
  single-register fallback when a device rejects a block read.
- Capability gating by Modbus register layout version (`v01.00`–`v01.03`) and
  by the hardware phase option.
- Dedicated heartbeat task writing `0x0D00 = 0x55AA` every five seconds in
  master mode, decoupled from the data poll.
- Charging control through one choke point: minimum 6 A, explicit opt-in for
  the "no limitation" value 0 A, the documented pause value, vendor write
  rate limits, and a deferred write so a dragged slider is not rejected.
- 36 sensors, 9 binary sensors, 1 number, 3 switches, 2 selects and 2 buttons,
  all translated to English and German including every enumerated state.
- `mennekes_amtron.set_charging_current` action with an `allow_unlimited`
  opt-in for the "no limitation" value.
- Diagnostics with an allowlist, a hashed serial number and the full raw
  register image.
- Four repair issues: lost energy-manager heartbeat (fixable, runs the
  documented recovery sequence), missing fallback configuration, an older
  register layout and a refused write.
- `scripts/smoke_modbus.py`, a read-only bus smoke test to run before the
  first write.
- An end-to-end test over a virtual serial link: a real Modbus RTU server at
  57600 baud, 8N2, device address 50, driven by the integration's own client.
- Four issue forms that ask for the register layout version, the control mode,
  the bus parameters and the diagnostics, so a report can be analysed without
  a round trip.
- `scripts/apply_repo_settings.py`, which declares the GitHub repository
  settings, the security options and the branch protection of `main` instead
  of leaving them undiffable in a web interface.
- Validation gates: repository, legal/provenance, quality scale, register map,
  pymodbus requirement, ruff, pytest, a 99 percent coverage ratchet and `mypy
  --strict`.
- All 52 official Home Assistant quality-scale rules answered, 43 `done` and
  9 `exempt`, with no rule left open. The gate rejects any rule going back to
  `todo` without being documented as a blocker.
