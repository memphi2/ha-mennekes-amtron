# MENNEKES AMTRON for Home Assistant (Unofficial)

[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.5%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

A local Home Assistant integration for MENNEKES AMTRON wallboxes that speak
**Modbus RTU** over RS-485 — full sensor coverage, safe charging control, a
config flow, diagnostics and repair issues. No cloud, no account, no polling
of anybody's server.

Developed and validated against an **AMTRON 4You 310 11 C2** (article
1313201205, 11 kW, fixed cable, 16 A) on Modbus register layout **v01.03**.

> This is an independent community project. It is not affiliated with,
> endorsed by, sponsored by, or certified by MENNEKES, Home Assistant, Nabu
> Casa or HACS.

## Why not the built-in `modbus:` integration

The built-in integration can read these registers, but it has no idea what
they mean. This one does:

- **`0x0302` has three meanings.** `0 A` is *no limitation* — the wallbox then
  signals its **maximum** current. `0.01–5.99 A` is *invalid* and makes it
  signal 0 A, which is the manufacturer's documented way to pause. `≥ 6 A` is
  a real limit. A YAML `number` with `min: 0` requests **full load** when a
  user drags the slider to zero. Here, the slider cannot go below 6 A at all.
- **The heartbeat is mandatory.** `0x0D00 = 0x55AA` has to arrive at least
  every 10 s or the wallbox goes into error state 200. This integration runs
  it in its own task, so a slow poll can never starve it.
- **Capability gating.** Registers appeared over four layout versions, and the
  phase switch needs 11 kW hardware. Entities exist only where the device
  actually has the register.
- **Rate limits.** The manufacturer asks for ≤ 1 current change per 5 s and
  > 5 min between pause, resume and phase switches. Both are enforced.

## Quick start (5 minutes)

1. Wire a USB RS-485 adapter to the wallbox: Modbus **A = +**, **B = −**.
2. Set the wallbox to satellite mode: **DIP bank S1, DIP 4 and DIP 5 to ON**,
   then restart the wallbox.
3. Find the adapter: `ls -l /dev/serial/by-id/`.
4. Install this repository as a HACS custom repository (category
   *Integration*) and restart Home Assistant.
5. Add **MENNEKES AMTRON** from *Settings → Devices & services*. If the
   device address or the bus parameters are not the factory ones, the setup
   form can **search the bus for you** — it only reads, so it cannot disturb
   the wallbox.
6. If the bus itself is suspect, the integration also ships a read-only check
   you can run on the server:

   ```bash
   docker exec -it homeassistant \
     python /config/custom_components/mennekes_amtron/smoke_modbus.py \
     /dev/serial/by-id/<your-adapter>
   ```

   See [docs/quickstart.md](docs/quickstart.md) for the other installation
   types.
7. The entry starts in **read-only** mode. Check the sensors, then switch the
   control mode to *Modbus master* in the entry options when you are ready.

Full instructions: [docs/quickstart.md](docs/quickstart.md).

## What you get

| Kind | Entities |
|---|---|
| Sensors | EVSE state, CP state, authorization, error code, signaled current, current/voltage/power L1–L3, total power, session energy/duration/max current, total energy, total sessions |
| Diagnostic sensors | Temperature, Modbus layout, downgrade current, house and EVSE current limits, energy-manager fallback, phase rotation, switched phases, cable lock, grid imbalance, Sunshine+ minimum current, phase-switching pause and mode, connected grid phases |
| Binary sensors | Vehicle connected, charging, **downgrade active**, error, energy-manager fallback, plus diagnostic capability flags |
| Controls | Charging-current limit (number), charging paused / charging release / wallbox locked (switches), solar mode and requested phases (selects), recover and restart (buttons) |
| Action | `mennekes_amtron.set_charging_current` with the explicit `allow_unlimited` opt-in |

Total energy (`0x1000`) feeds the Energy dashboard. The manufacturer marks it
*not usable for billing*; the entity description says so too.

## §14a EnWG

The grid-operator downgrade is enforced **in the wallbox hardware**, over the
XG1 input, and limits the charging current regardless of what Modbus asks for.
This integration never tries to work around it — it makes it **visible**
through `binary_sensor.*_downgrade_active` and the downgrade-current sensor.

If your §14a control box is itself a Modbus master on the same RS-485 bus,
leave this integration in read-only mode: RS-485 has exactly one master. See
[docs/safety.md](docs/safety.md).

## Documentation

Everything is indexed in **[docs/README.md](docs/README.md)**. The short list:

| | |
|---|---|
| [Hardware installation](docs/hardware.md) | Cabling, DIP switches, the §14a input, USB passthrough |
| [Quick start](docs/quickstart.md) | Installation in Home Assistant and the first charge |
| [Entity reference](docs/entities.md) | Every entity, what it means, when it exists |
| [Automations](docs/automations.md) | PV surplus, load management, phase switching, §14a |
| [User guide](docs/user-guide.md) | Options, actions, how data is updated |
| [Troubleshooting](docs/troubleshooting.md) | When it does not work |
| [Safety and limitations](docs/safety.md) | Read before enabling control |
| [Modbus register map](docs/modbus-registers.md) | The device contract |
| [Architecture](docs/architecture.md) · [Quality scale](docs/quality-scale.md) · [Repository settings](docs/repository-settings.md) · [Legal notes](docs/legal.md) | For maintainers |

## Legal Notes

The project code and documentation are released under the Apache License,
Version 2.0. MENNEKES, AMTRON, Home Assistant, HACS, Nabu Casa and Modbus are
trademarks or names of their respective owners and are used descriptively for
compatibility and attribution only.

Manufacturer documentation is **referenced, never vendored**: no MENNEKES PDF,
firmware image or configuration-tool payload belongs in this repository.
Register addresses and numeric values are facts; the specification's prose is
not, and is rewritten in the project's own words. See
[docs/legal.md](docs/legal.md) and [NOTICE](NOTICE).

## AI Assistance

Parts of this repository were written with AI assistance. Every register
value, protocol rule and enumerated state was taken from the manufacturer's
Modbus RTU specification (document revision 2.5, 2025-07-11, layout v01.03)
and cross-checked against the pymodbus API, not from model memory. The code,
the gates and the tests are reviewed and maintained by humans.
