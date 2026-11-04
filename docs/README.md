# Documentation

- [Independent Modbus service](modbus-service.md): keep heartbeat and charging state through HA Core restarts.

An unofficial Home Assistant integration for MENNEKES AMTRON wallboxes over
local Modbus RTU.

## Start here

| I want to… | Read |
|---|---|
| check models and hardware test status | [Device compatibility](compatibility.md) |
| prepare the installation with an electrician | [Hardware installation](hardware.md) |
| install, update or remove the integration | [Installation and updates](installation.md) |
| add the wallbox and test the first charge | [Quick start](quickstart.md) |
| upgrade an existing 0.1.0 installation | [Upgrading](upgrading.md) |
| know what every entity means | [Entity reference](entities.md) |
| use a ready-made automation | [Blueprints](blueprints.md) |
| set up a tariff schedule or action | [Automations](automations.md) |
| reduce database growth and configure history retention | [Recorder](recorder.md) |
| understand the options and the daily use | [User guide](user-guide.md) |
| fix something that is broken | [Troubleshooting](troubleshooting.md) |
| know what can go wrong with a wallbox | [Safety and limitations](safety.md) |

## Reference

| Document | What it is |
|---|---|
| [Modbus register map](modbus-registers.md) | Every register the integration implements, the enumerated values, the read blocks and the rules the device imposes |
| [Architecture](architecture.md) | How the code is laid out and the four decisions behind it |
| [Quality scale](quality-scale.md) | The Home Assistant quality-scale status, rule by rule |
| [Maintenance validation](validation.md) | Resource measurements, release integrity, compatibility, privacy and serial fault checks |
| [Repository settings](repository-settings.md) | Branch protection and the security options, declared as code |
| [Legal notes](legal.md) | Licence, trademarks, and why no vendor document is in this repository |
| [Provenance audit](audits/current-legal-provenance.md) | Source and licensing evidence |
| [November compatibility audit](ha-2026-11-validation.md) | Local compatibility, Recorder and repository validation results |

## Before you start

**Read-only first.** A new config entry never writes and never sends the
heartbeat. Look at the sensors, confirm they match the wallbox display, and
only then switch the control mode to *Modbus master*. See
[quickstart.md](quickstart.md).

**One master per bus.** Modbus RTU allows exactly one. If a §14a control box or
another energy manager already drives your bus, this integration must not
share it. Read-only mode still sends requests. See [safety.md](safety.md).

**0 A does not mean "stop".** Writing 0 A to the charging-current register
removes the limit and makes the wallbox signal its *maximum*. The integration
accepts 0 in **Charging current limit** to turn the limit off. Use
**Charging paused** to stop charging. See
[safety.md](safety.md#the-0-a-trap).
