# Documentation

An unofficial Home Assistant integration for MENNEKES AMTRON wallboxes over
local Modbus RTU.

## Start here

| I want to… | Read |
|---|---|
| wire it up: cabling, DIP switches, USB passthrough | [Hardware installation](hardware.md) |
| get it running in Home Assistant | [Quick start](quickstart.md) |
| know what every entity means | [Entity reference](entities.md) |
| build automations | [Automations](automations.md) |
| understand the options and the daily use | [User guide](user-guide.md) |
| fix something that is broken | [Troubleshooting](troubleshooting.md) |
| know what can go wrong with a wallbox | [Safety and limitations](safety.md) |

## Reference

| Document | What it is |
|---|---|
| [Modbus register map](modbus-registers.md) | Every register the integration implements, the enumerated values, the read blocks and the rules the device imposes |
| [Architecture](architecture.md) | How the code is laid out and the four decisions behind it |
| [Quality scale](quality-scale.md) | The Home Assistant quality-scale status, rule by rule |
| [Repository settings](repository-settings.md) | Branch protection and the security options, declared as code |
| [Legal notes](legal.md) | Licence, trademarks, and why no vendor document is in this repository |
| [Provenance audit](audits/current-legal-provenance.md) | The technical audit snapshot behind those claims |

## The three things worth knowing before you start

**Read-only first.** A new config entry never writes and never sends the
heartbeat. Look at the sensors, confirm they match the wallbox display, and
only then switch the control mode to *Modbus master*. See
[quickstart.md](quickstart.md).

**One master per bus.** RS-485 allows exactly one. If a §14a control box or
another energy manager already drives your bus, this integration has to stay
read-only. See [safety.md](safety.md).

**0 A does not mean "stop".** Writing 0 A to the charging-current register
removes the limit and makes the wallbox signal its *maximum*. The integration
keeps that value out of reach; it is worth knowing why. See
[safety.md](safety.md#the-0-a-trap).
