# Mennekes Amtron RTU documentation

Choose the task you want to complete. Basic charging needs no vehicle cloud
account, calendar, tariff import or YAML configuration.

## Start here

1. [Install and choose a connection](installation.md): the service app is
   recommended but optional; direct USB and LAN remain supported.
2. [Check the wallbox and try a first charge](quickstart.md).
3. [Use current control and pause/resume](user-guide.md).
4. [Add optional functions](features.md) only when you need them.

Already installed? Keep your device and entity IDs. Use
[Updates and connection changes](upgrading.md) instead of adding the wallbox again.

## Find the right guide

| Your task | Guide |
|---|---|
| Install the recommended service app | [Modbus service](modbus-service.md) |
| Use a LAN-to-RS-485 adapter | [LAN connection](network-gateway.md) |
| Select one car, several cars or optional automatic detection | [Vehicle setup](vehicle-setup.md) |
| Reach a charge target for the next trip | [Departure planning](departure-planning.md) |
| Use a calendar, climate action or PV surplus | [Blueprints](blueprints.md) |
| Estimate charging costs or losses | [Charging estimates](charging-estimates.md) |
| Find a control, sensor or template state | [Entity reference](entities.md) |
| Reduce Recorder/database growth | [Recorder guide](recorder.md) |
| Resolve a missing control, heartbeat fault or unclear vehicle match | [Troubleshooting](troubleshooting.md) |
| Understand wiring, supported models and operating limits | [Hardware](hardware.md), [compatibility](compatibility.md), [safety](safety.md) |

## Versions and feature availability

These pages follow the current development branch. A feature described here may
not yet be present in the release installed in Home Assistant.

| Version | Available functionality |
|---|---|
| **Stable 0.6.1** | USB/LAN, optional service app, normal controls, kWh/percentage targets, next-trip planning, calendar/BavarianData timer import, costs and losses. |
| **Published beta 0.7.0b4** | Separate feature menus, optional supported Waveshare setup/TCP login and additional control/recovery fixes. |
| **Unreleased changes in this branch** | Central vehicle profiles, manual or optional automatic profile selection, shared BavarianData telemetry, live app Modbus debugging, improved configuration dialogs and corrected guided-page support for Supervisor 2026.10.4+. |

Published app releases through 0.7.0b4 still require Supervisor 2026.11.0 to open
the guided page. If that page rejects your current stable Supervisor, use the
app's native **Configuration** tab; see [the version-specific explanation](upgrading.md#app-connection-page).
Installing an existing beta does not include changes marked **unreleased**.

The integration and app do not need matching version numbers. Follow the
release's minimum-service requirement and [update instructions](installation.md#updates).
Switching app catalogs creates a separate app and requires a controlled handover.

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
  [maintenance validation](validation.md) and [Recorder validation](recorder-validation.md)
- [Service publication](service-releasing.md), [repository settings](repository-settings.md)
  and [November compatibility audit](ha-2026-11-validation.md)
- [Legal information](legal.md) and [provenance evidence](audits/current-legal-provenance.md)
