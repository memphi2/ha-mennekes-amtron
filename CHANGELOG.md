# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
- 35 sensors, 9 binary sensors, 1 number, 3 switches, 2 selects and 2 buttons,
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
