# Wallbox guide

Start with these four pages. Basic charging needs no vehicle account, calendar,
tariff import or YAML.

| What do you want to do? | Open |
|---|---|
| Install or update the wallbox integration | **[Installation](installation.md)** |
| Set current, pause or resume charging | **[Everyday charging](user-guide.md)** |
| Add the wallbox to your HA dashboard | **[Dashboard cards — YAML in five languages](app-from-home-assistant.md#dashboard-with-mushroom-cards)** |
| Fix a problem | **[Troubleshooting](troubleshooting.md)** |

The optional **[service app](modbus-service.md)** is recommended to keep the
heartbeat running through Home Assistant Core restarts. Already using it?
[Open the guide to its web interface](app-guide.md).

## Add a function when you need it

- **[Vehicles](vehicle-setup.md):** vehicle data, selection, automatic recognition
  and virtual Plug & Charge.
- **[Departure](departure-planning.md):** a charging target for your next trip.
- **[Solar and calendar](blueprints.md):** ready-to-use automations.
- **[Costs and charging losses](charging-estimates.md):** optional estimates.

Unsure what to enable? [Choose optional functions](features.md).

## Three things to remember

- **0 A removes the current limit.** Use **Charging paused** to pause.
- **Enable writes in one place.** Service: app Read-only off. Direct: integration
  Control mode set to Modbus master.
- **One owner uses the bus.** Stop the old owner before starting another;
  read-only mode still sends requests.

<details>
<summary>Connection details and reference</summary>

- [First charge walkthrough](quickstart.md)
- [USB wiring and hardware](hardware.md), [supported models](compatibility.md)
  and [operating limits](safety.md)
- [LAN connection](network-gateway.md) and [adapter settings](lan-adapters.md)
- [Standalone service](service-standalone.md) and [service configuration](service-reference.md)
- [All entities](entities.md), [automation actions](automations.md)
  and [Recorder settings](recorder.md)

</details>

<details>
<summary>Development, validation and legal information</summary>

- [Architecture](architecture.md) and [Modbus registers](modbus-registers.md)
- [Quality scale](quality-scale.md), [validation](validation.md),
  [Recorder validation](recorder-validation.md) and [charging conflict matrix](charging-conflict-matrix.md)
- [Service publication](service-releasing.md), [repository settings](repository-settings.md)
  and [November compatibility audit](ha-2026-11-validation.md)
- [Legal information](legal.md) and [provenance evidence](audits/current-legal-provenance.md)

</details>
