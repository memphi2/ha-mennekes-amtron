# Mennekes Amtron: user guide

Start with normal charging. Add vehicle data, a departure time or automation only
when you need it. You do not need a vehicle account or a dashboard card to begin.

## Start here

| Your next step | Guide |
|---|---|
| Connect a wallbox for the first time | **[Installation](installation.md)** — choose the app or a direct connection, then connect USB or LAN |
| Check that charging works | **[First charge](quickstart.md)** — current, pause, resume and heartbeat |
| Use the wallbox every day | **[Charging](user-guide.md)** — normal charging, limits and guest start |
| Understand what changed the charge | **[Activity timeline](troubleshooting.md#understand-the-activity-timeline)** — command, trigger, time and observed result |
| Put the controls on a dashboard | **[Dashboard YAML](app-from-home-assistant.md#dashboard-with-mushroom-cards)** — basic and Mushroom examples in five languages |
| Something is not working | **[Troubleshooting](troubleshooting.md)** — start with the symptom |

**Already installed?** Use [Updates](installation.md#updates); keep your wallbox
entry. Integration and app updates are separate.

## Add an optional function

- **[App web interface](app-guide.md):** see what is happening and change shared limits.
- **[Vehicle data](vehicle-setup.md):** percentage targets, several cars, optional
  automatic recognition and virtual Plug & Charge.
- **[Ready for the next trip](departure-planning.md):** a one-time departure,
  manual or imported from a calendar or vehicle timer.
- **[Costs and losses](charging-estimates.md):** choose the available price and vehicle data.
- **[Automations](blueprints.md):** solar charging, calendar import and preconditioning.

[Which feature starts or stops charging?](features.md#which-function-may-start-or-stop-charging)

The **optional service app is recommended** for HA Core restart continuity. A
direct connection offers the same features while HA is running. [Compare the setups](installation.md#1-choose-your-connection).

## Reference

Look up a detail when you need it: [models](compatibility.md), [wiring](hardware.md),
[LAN adapters](network-gateway.md), [entities](entities.md), [actions](automations.md),
[history and database size](recorder.md), [operating limits](safety.md).

<details>
<summary>Development, protocols and project information</summary>

[Architecture](architecture.md) · [Service API](service-reference.md) ·
[Standalone deployment](service-standalone.md) · [Modbus registers](modbus-registers.md) ·
[Validation](validation.md) · [Control conflicts](charging-conflict-matrix.md) ·
[Recorder validation](recorder-validation.md) · [Release process](service-releasing.md) ·
[Repository settings](repository-settings.md) · [HA compatibility](ha-2026-11-validation.md) ·
[Quality scale](quality-scale.md) · [Legal](legal.md) · [Provenance](audits/current-legal-provenance.md)

</details>
