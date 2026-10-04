# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Changed

- Configuration registers are no longer read on every poll. Eight of the
  eighteen read blocks carry values that change when somebody reconfigures the
  wallbox, not while it charges — the serial number, the article number, the
  DIP-configured limits, the hardware phase option, the lifetime counters. The
  coordinator now reads those once a minute and carries them forward, which
  leaves the bus to the ten blocks that actually move. A reconfiguration still
  shows up within a minute.
- `ruff` now selects 38 rulesets instead of nine, every one of them a ruleset
  the integration already passes completely, including the bandit security
  rules `S`, `BLE`, `ASYNC`, `SLF` and `PTH`. A regression in any of them now
  fails the build.

### Fixed

- Repair issues were written to the issue registry on every coordinator
  update, roughly seventeen thousand times a day, even when nothing had
  changed. They are now written only when the issue or its placeholders
  actually change.

### Added

- CI validates every push against the next Home Assistant pre-release in a job
  that is allowed to fail, so a breaking change in Core is noticed before it
  ships.
- `SUPPORT.md` records why the minimum Home Assistant version is where it is,
  and which features set that floor.

## 0.2.1

### Fixed

- 38 entities declared an icon although their device class already provides
  one, and for several of those the device class provides a *state-dependent*
  one: a `plug` shows connected or disconnected, a `problem` shows alert or
  ok, a `battery_charging` shows the charge state. Declaring an icon
  overrode all of that with a static picture. Icons are now declared only
  where no device class gives one — which means no device class at all, or
  the `enum` device class, which has none. `scripts/check_register_map.py`
  enforces the rule in both directions: an entity without a device-class icon
  must declare one, an entity with one must not.
- The phase-switching pause sensor had no display precision, so a whole
  number of seconds could render with decimals.

### Added

- `docs/hardware.md`: cabling and RS-485 practice, the DIP switches (satellite
  mode, installation current, the §14a downgrade percentage, solar mode), the
  XG1 downgrade input, USB passthrough for every Home Assistant installation
  type, and how to read the DIP-configured values back from the diagnostic
  sensors. Every statement is marked as coming from the Modbus specification
  or from the installation manual, and where a detail depends on a DIP
  numbering this project cannot verify, it says so instead of guessing. A
  repository check keeps those markers in place.

## 0.2.0

### Added

- The setup form can **search the bus**. When the values entered do not work,
  the flow tries every documented device address at those bus parameters, or
  every documented combination of address, baud rate and frame, and offers
  what it found with its layout version and serial number. It runs as a
  progress step with an estimate, tries the user's own values first, and only
  reads, so it cannot disturb a wallbox. The device address and the bus
  parameters are configurable with the MENNEKES configuration tool, so an
  inherited installation rarely uses the factory values -- and guessing them
  by hand was the first thing this integration asked of a user.
- `docs/entities.md`, a reference for every entity: what it means, its unit
  and class, what the wallbox has to support for it to exist, and the
  enumerated states automations will see. It is generated from the entity
  descriptions, and `scripts/check_register_map.py` fails the build when an
  entity is missing from it.
- `docs/automations.md` with working recipes: PV surplus charging, pause and
  resume with the manufacturer's hysteresis, dynamic load management, cheap
  tariff windows, single-phase switching, noticing a §14a downgrade, and
  recovering from a lost heartbeat.
- `docs/troubleshooting.md`, worked through in the order a fault actually
  presents itself.
- `docs/README.md`, an index with reading paths.

## 0.1.3

### Fixed

- The bus check could not be run where it is needed. It lived in `scripts/`,
  so it only existed on a checkout of this repository — while the USB RS-485
  adapter is plugged into the machine Home Assistant runs on. It now ships
  with the integration, at
  `/config/custom_components/mennekes_amtron/smoke_modbus.py`, and imports
  nothing from the integration, so it runs as a plain script with Home
  Assistant's own Python, which already has pymodbus.

### Added

- `--scan` tries every documented device address from 10 to 50, and
  `--scan-baudrate` every documented baud rate as well, for a wallbox whose
  configuration somebody changed with the MENNEKES configuration tool. The
  check prints how long a scan will take before it starts.
- The check now also reports the EVSE state in words, and says which values to
  enter in the config flow when the bus is fine.

### Changed

- The quickstart gives the exact command per installation type — Home
  Assistant OS, Supervised, Container and Core — for both finding the adapter
  and running the check.

## 0.1.2

### Changed

- The integration is now called `MENNEKES AMTRON (Unofficial)` in Home
  Assistant's integration list, which is where somebody decides what they are
  installing. `hacs.json` already said that; `manifest.json` did not, and the
  manifest is the one Home Assistant shows. A repository check now fails when
  the two names disagree or when the manifest drops the marker.

  The config entry title stays `MENNEKES AMTRON`. It becomes the device name
  and is prefixed to every entity id, so the marker belongs in the list, not
  in `sensor.mennekes_amtron_unofficial_voltage_l1`.

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
- A read-only bus check to run before the first write.
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
