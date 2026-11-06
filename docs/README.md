# Mennekes Amtron RTU documentation

Connect your wallbox, control charging and track its energy use in Home
Assistant. Communication stays local over RS-485.

Reach the RS-485 bus through USB or a [LAN adapter](network-gateway.md).
A LAN connection requires no local USB device.

## Get started

1. Check your model in the [compatibility matrix](compatibility.md) and prepare
   the connection using the [hardware guide](hardware.md).
2. [Install the integration](installation.md), then follow the
   [Quick start](quickstart.md).
3. Open the [User guide](user-guide.md) for the current slider, pause switch,
   readings and options.

You can connect the serial adapter directly to the integration or use the
**optional, recommended [Modbus service](modbus-service.md)**. The service keeps
the heartbeat and last charging state through Home Assistant Core restarts.
An app shutdown, host restart or serial failure still requires the wallbox's
configured fallback.

## Use your wallbox

| I want to… | Read |
|---|---|
| Set current, pause charging or change options | [User guide](user-guide.md) |
| Understand a sensor or find its template states | [Entity reference](entities.md) |
| Charge from PV surplus | [Blueprints](blueprints.md) |
| Set up a tariff schedule or action | [Automations](automations.md) |
| Estimate charging losses or costs | [Charging estimates](charging-estimates.md) |
| Use the Energy dashboard and reduce database growth | [Recorder](recorder.md) |
| Solve a connection, control or heartbeat problem | [Troubleshooting](troubleshooting.md) |
| Update or remove the integration | [Installation and updates](installation.md) |
| Understand hardware limits and fallback behavior | [Safety and limitations](safety.md) |

## Three settings worth knowing

- **0 A removes the current limit.** Use **Charging paused** to stop charging.
- **Write access has one setting.** With direct USB or LAN, select **Modbus master**
  in the integration options. With the service, disable **Read-only** in the
  app configuration, save and restart the app. HA follows its next status update
  without a Core restart.
- **One connection owns the bus.** Read-only mode still sends requests. Do not
  run direct serial and the service against the same adapter at the same time.

## Advanced setup and reference

| Document | What you will find |
|---|---|
| [Standalone service](service-standalone.md) | Run the service outside a Home Assistant app |
| [LAN adapter setup](network-gateway.md) | Transparent RTU over TCP or a translating Modbus TCP-to-RTU gateway |
| [Service reference](service-reference.md) | Service configuration and technical details |
| [Updates and switching connections](upgrading.md) | Updates and moving an existing wallbox to the service |
| [Modbus register map](modbus-registers.md) | Register meanings, values and read blocks |
| [Architecture](architecture.md) | How the integration and service fit together |
| [Quality scale](quality-scale.md) | Home Assistant quality-scale status |
| [Maintenance validation](validation.md) | Compatibility, resource, privacy and release checks |
| [Recorder validation](recorder-validation.md) | Database measurements, retention tests and their limits |
| [Service publication](service-releasing.md) | App channels and container publication |
| [Repository settings](repository-settings.md) | Branch protection and security requirements |
| [Legal notes](legal.md) | Licence and trademark information |
| [Provenance audit](audits/current-legal-provenance.md) | Source and licensing evidence |
| [November compatibility audit](ha-2026-11-validation.md) | Recorded compatibility and Recorder validation |
