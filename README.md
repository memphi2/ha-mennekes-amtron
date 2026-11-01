# MENNEKES AMTRON RTU for Home Assistant (Unofficial)

[![Marketplace: Custom](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg)](docs/installation.md)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Read charging status, power and energy from a MENNEKES AMTRON wallbox and
control its current, pause/resume and supported phase settings in Home
Assistant. Communication is local over **Modbus RTU / RS-485**. The wallbox
connection needs no cloud account; Marketplace custom-repository installation
can require a GitHub connection.

Developed for the **AMTRON 4You 310 11 C2** (article 1313201205, 11 kW,
fixed cable, 16 A), register layout **v01.03**. Automated validation uses real
Home Assistant and simulated RTU devices. Physical-wallbox validation remains
outstanding. See [supported devices and limitations](docs/user-guide.md#compatibility-and-limitations).

> This is an independent community project. It is not affiliated with,
> endorsed by, sponsored by, or certified by MENNEKES, Home Assistant, Nabu
> Casa or HACS.

## Before you start

- Use an **exclusive RS-485 bus**. Read-only mode also sends requests and
  cannot share the bus with another master.
- Start read-only and check the readings. Before enabling control, configure
  the wallbox's fallback for a stopped Home Assistant heartbeat.
- Use **Charging paused** to stop charging. **0 A removes the current limit**.

Electrical work belongs with a qualified electrician. Read
[Safety and limitations](docs/safety.md) and the [hardware guide](docs/hardware.md).

## Installation

**Upgrading from 0.1.0?** Version 0.2.0 uses the new integration domain
`mennekes_amtron_rtu` and requires reconfiguration. Follow the
[upgrade guide](docs/upgrading.md) before updating.

Before setup, connect the RS-485 adapter and configure the wallbox as a
Modbus satellite. Follow [hardware installation](docs/hardware.md) and use
an exclusive bus: read-only mode also sends requests.

### Home Assistant Marketplace (2026.11 and newer)

[![Open your Home Assistant instance and open this repository in the Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=memphi2&repository=ha-mennekes-amtron&category=integration)

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

## Everyday use

| I want to… | Use |
|---|---|
| Set the charging rate | **Charging current limit**, from 6 A to the configured cap |
| Pause or resume | **Charging paused**: on pauses, off resumes after the waiting interval |
| Track consumption | **Total energy** in the Energy dashboard; not usable for billing |
| Charge from PV surplus | The two [PV blueprints](docs/blueprints.md), with a suitable power-budget sensor |
| See a grid-operator restriction | **Downgrade active** and **Downgrade current**; the wallbox applies the restriction in hardware |
| Reduce database growth | Keep the 30-second measurement default and choose a [Recorder profile](docs/recorder.md) |

Controls appear in **Modbus master** mode. The integration enforces current
write intervals and pause/resume waiting times. Setting **Solar charging mode**
alone does not implement PV control. Follow the [user guide](docs/user-guide.md)
for behavior, configuration and limits.

## Documentation

Everything is indexed in **[docs/README.md](docs/README.md)**. The short list:

| | |
|---|---|
| [Hardware installation](docs/hardware.md) | Cabling, DIP switches, the §14a input, USB passthrough |
| [Quick start](docs/quickstart.md) | Installation in Home Assistant and the first charge |
| [Entity reference](docs/entities.md) | Every entity, what it means, when it exists |
| [Blueprints](docs/blueprints.md) | Four ready-made automations, installed with the integration |
| [Automations](docs/automations.md) | Tariff schedules, phase changes and action examples |
| [User guide](docs/user-guide.md) | Daily controls, options and actions |
| [Recorder](docs/recorder.md) | History retention, statistics and database growth |
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
