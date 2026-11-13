# Mennekes Amtron RTU documentation

Choose the task you want to complete. Basic charging needs no vehicle cloud
account, calendar, tariff import or YAML configuration.

## Start here

1. [Install and choose a connection](installation.md): the service app is
   recommended but optional; direct USB and LAN remain supported.
2. [Check the wallbox and try a first charge](quickstart.md).
3. [Use current control and pause/resume](user-guide.md).
4. [Add optional functions](features.md) only when you need them.

Already installed? Keep your device and entity IDs and follow
[Updates](installation.md#updates) instead of adding the wallbox again.

## Find the right guide

| Your task | Guide |
|---|---|
| Install the recommended service app | [Modbus service](modbus-service.md) |
| Use the app's overview, charging jobs or guest charging | [The app's web pages](app-guide.md) |
| Use a LAN-to-RS-485 adapter | [LAN connection](network-gateway.md) |
| Select one car, several cars or optional automatic detection | [Vehicle setup](vehicle-setup.md) |
| Automatically release charging for a recognized profile | [Virtual Plug & Charge](vehicle-setup.md#optional-virtual-plug--charge) |
| Reach a charge target for the next trip | [Departure planning](departure-planning.md) |
| Use a calendar, climate action or PV surplus | [Blueprints](blueprints.md) |
| Estimate charging costs or losses | [Charging estimates](charging-estimates.md) |
| Find a control, sensor or template state | [Entity reference](entities.md) |
| Reduce Recorder/database growth | [Recorder guide](recorder.md) |
| Resolve a missing control, heartbeat fault or unclear vehicle match | [Troubleshooting](troubleshooting.md) |
| Understand wiring, supported models and operating limits | [Hardware](hardware.md), [compatibility](compatibility.md), [safety](safety.md) |

## Version

These pages describe **0.9.0b2** of the integration and the app. Install both
in the same version; in service mode, update the app first. The app's web pages
need **Supervisor 2026.10.4 or newer**. Optional features stay off until you set
them up.

## Three settings to remember

- **0 A removes the current limit.** Use **Charging paused** to pause.
- **Enable writes in one place.** Service: app Read-only off. Direct: integration
  Control mode set to Modbus master.
- **One owner uses the bus.** Stop the old owner before starting another;
  read-only mode still sends requests.

## Advanced reference

- [Standalone service](service-standalone.md), [service configuration](service-reference.md)
  and [LAN adapter configuration](lan-adapters.md)
- [Automation actions](automations.md) and [Modbus register map](modbus-registers.md)
- [Architecture](architecture.md), [quality scale](quality-scale.md),
  [maintenance validation](validation.md), [Recorder validation](recorder-validation.md)
  and the [charging conflict matrix](charging-conflict-matrix.md)
- [Service publication](service-releasing.md), [repository settings](repository-settings.md)
  and [November compatibility audit](ha-2026-11-validation.md)
- [Legal information](legal.md) and [provenance evidence](audits/current-legal-provenance.md)
- Review: [failures and restart](reviews/2026-11-failures-and-restart.md)
