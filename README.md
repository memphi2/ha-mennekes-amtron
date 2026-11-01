# MENNEKES AMTRON RTU for Home Assistant (Unofficial)

[![Marketplace: Custom](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg)](docs/installation.md)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

A local Home Assistant integration for MENNEKES AMTRON wallboxes that speak
**Modbus RTU** over RS-485 — full sensor coverage, safe charging control, a
config flow, diagnostics and repair issues. No cloud, no account, no polling
of anybody's server.

Developed for the **AMTRON 4You 310 11 C2** (article 1313201205, 11 kW,
fixed cable, 16 A) on Modbus register layout **v01.03**. Automated validation
uses a virtual Modbus RTU server and real Home Assistant; it does not replace
verification on a physical wallbox.

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
  it in its own task with priority access between bus transactions.
- **Capability gating.** Registers appeared over four layout versions, and the
  phase switch needs 11 kW hardware. Entities exist only where the device
  actually has the register.
- **Rate limits.** The manufacturer asks for ≤ 1 current change per 5 s and
  > 5 min between pause, resume and phase switches. Both are enforced.

## Installation

**Upgrading from 0.1.0?** Version 0.2.0 uses the new integration domain
`mennekes_amtron_rtu` and requires reconfiguration. Follow the
[upgrade guide](docs/upgrading.md) before updating.

Before setup, connect the RS-485 adapter and configure the wallbox as a
Modbus satellite. Follow [hardware installation](docs/hardware.md) and use
an exclusive bus: read-only mode also sends requests.

### Home Assistant Marketplace (2026.11 and newer)

1. Open **Settings → Marketplace** and accept the community-content notice
   when requested.
2. Search for **MENNEKES AMTRON RTU (Unofficial)** by **memphi2**.
3. While the catalog submission is pending, add
   `https://github.com/memphi2/ha-mennekes-amtron` as a custom repository,
   category **Integration**. The [installation guide](docs/installation.md#home-assistant-marketplace)
   includes a direct Marketplace link; custom repositories require a GitHub connection.
4. Install the integration, then add **MENNEKES AMTRON RTU (Unofficial)** under
   **Settings → Devices & services** and enter the adapter and bus settings.

A first Marketplace installation normally needs no restart. Complete the
requested restart after an update or removal of loaded integration code.

### HACS (Home Assistant 2026.9 and 2026.10)

Search HACS for the same integration. If it is not listed, add the repository
above under **Custom repositories**, category **Integration**. Download it,
restart Home Assistant, then add the integration under **Devices & services**.

### Existing installations, updates and removal

Keep existing **0.2.x** entries when moving from HACS to the Marketplace.
Updates appear under **Settings → Updates** on 2026.11+. Read the release
notes and account for the heartbeat interruption during a restart.

The entry starts in **read-only** mode. Follow the [quick start](docs/quickstart.md)
to check readings before enabling *Modbus master* and trying the first charge.
See [installation, updates and removal](docs/installation.md) for the complete
workflow, including the pending catalog submission and existing installations.

## What you get

| Kind | Entities |
|---|---|
| Sensors | EVSE state, CP state, authorization, error code, signaled current, current/voltage/power L1–L3, total power, session energy/duration/max current, total energy, total sessions |
| Diagnostic sensors | Temperature, Modbus layout, downgrade current, house and EVSE current limits, energy-manager fallback, phase rotation, switched phases, cable lock, grid imbalance, Sunshine+ minimum current, phase-switching pause and mode, connected grid phases |
| Binary sensors | Vehicle connected, charging, **downgrade active**, error, energy-manager fallback, plus diagnostic capability flags |
| Controls | Charging-current limit (number), charging paused / charging release / wallbox locked (switches), solar mode and requested phases (selects), recover and restart (buttons) |
| Action | `mennekes_amtron_rtu.set_charging_current` with the explicit `allow_unlimited` opt-in |

Total energy (`0x1000`) feeds the Energy dashboard. The manufacturer marks it
*not usable for billing*; the entity description says so too.

## §14a EnWG

The grid-operator downgrade is enforced **in the wallbox hardware**, over the
XG1 input, and limits the charging current regardless of what Modbus asks for.
This integration never tries to work around it — it makes it **visible**
through `binary_sensor.*_downgrade_active` and the downgrade-current sensor.

If your §14a control box is itself a Modbus master on the same RS-485 bus,
do not run this integration on that bus, even in read-only mode: it sends
read requests, and Modbus RTU permits only one master on the bus. See
[docs/safety.md](docs/safety.md).

## Documentation

Everything is indexed in **[docs/README.md](docs/README.md)**. The short list:

| | |
|---|---|
| [Hardware installation](docs/hardware.md) | Cabling, DIP switches, the §14a input, USB passthrough |
| [Quick start](docs/quickstart.md) | Installation in Home Assistant and the first charge |
| [Entity reference](docs/entities.md) | Every entity, what it means, when it exists |
| [Blueprints](docs/blueprints.md) | Four ready-made automations, installed with the integration |
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
