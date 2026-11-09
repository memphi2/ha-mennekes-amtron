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
| Automatically release charging for a recognized profile | [Virtual Plug & Charge](vehicle-setup.md#optional-virtual-plug--charge) — beta 0.8.0b4 |
| Reach a charge target for the next trip | [Departure planning](departure-planning.md) |
| Use a calendar, climate action or PV surplus | [Blueprints](blueprints.md) |
| Estimate charging costs or losses | [Charging estimates](charging-estimates.md) |
| Find a control, sensor or template state | [Entity reference](entities.md) |
| Reduce Recorder/database growth | [Recorder guide](recorder.md) |
| Resolve a missing control, heartbeat fault or unclear vehicle match | [Troubleshooting](troubleshooting.md) |
| Understand wiring, supported models and operating limits | [Hardware](hardware.md), [compatibility](compatibility.md), [safety](safety.md) |

## Versions and feature availability

These pages cover **beta 0.8.0b7** and identify features already available in
**stable 0.6.1**. Check your installed integration and app versions before looking
for a new option.
Beta 2 adds a separate app **Debug log** tab, a wallbox **Virtual Plug & Charge**
activation switch and guided vehicle setup. Beta 3 lets Virtual Plug & Charge end
a manual pause for a recognized car, releases charging without the state-change
delay and shows why charging stays blocked. Beta 4 keeps entities available
across a single disturbed Modbus read, keeps the selected car after unplugging
and remembers each car's percentage target.

| Version | Available functionality |
|---|---|
| **Stable 0.6.1** | USB/LAN, optional service app, normal controls, kWh/percentage targets, next-trip planning, calendar/BavarianData timer import, costs and losses. |
| **Beta 0.8.0b7** | Central vehicle profiles, manual or optional automatic selection, optional Virtual Plug & Charge, shared BavarianData telemetry, live app Modbus debugging, a wallbox Plug & Charge switch, guided vehicle settings, supported Waveshare setup/TCP login and further control/recovery fixes. |

App **0.8.0b2** supports its guided page on **Supervisor 2026.10.4+**. Update an
older beta app if it incorrectly asks for Supervisor 2026.11.0. The separate
live Modbus tab needs app **0.8.0b2**; Virtual Plug & Charge needs the service
capability in **app 0.8.0b1 or newer**. Both features are optional and off by default.
See [the beta update guide](upgrading.md#trying-080b6).

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
