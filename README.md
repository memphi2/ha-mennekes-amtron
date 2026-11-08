# Mennekes Amtron RTU

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Marketplace](https://img.shields.io/badge/Home_Assistant-Marketplace-41BDF5?style=flat-square)](https://contributors.home-assistant.io/marketplace/)
[![Quality: Platinum (self-assessed)](https://img.shields.io/badge/Quality-Platinum%20%28self--assessed%29-c8cdd3?labelColor=3d4852)](docs/quality-scale.md)

Control your Mennekes Amtron in Home Assistant: set the charging current, pause
charging, track energy and add solar, vehicle or departure-based charging when
you need it. Communication stays local through **USB/RS-485 or a LAN adapter**.

**[Install](docs/installation.md)** · **[Everyday use](docs/user-guide.md)** · **[Optional functions](docs/features.md)** · **[Get help](docs/troubleshooting.md)**

Integration and app available in **English, German, French, Dutch and Italian**.

## Supported wallboxes

- **Amtron 4You 300**
- **Amtron Compact 2.0s**
- **Amtron Start 2.0s**

See the [model matrix](docs/compatibility.md) for exact variants and reported
hardware tests. A LAN adapter changes the connection, not the supported wallbox
families.

## Get started

[![Open in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=memphi2&repository=ha-mennekes-amtron&category=integration)

1. [Prepare the wallbox and adapter](docs/hardware.md), then install **Mennekes
   Amtron RTU** by **memphi2** through the Marketplace on HA 2026.11+ or HACS on
   HA 2026.9/2026.10.
2. Choose the **optional, recommended [Modbus service app](docs/modbus-service.md)**
   or a **[direct USB/LAN connection](docs/quickstart.md#5-add-the-wallbox)**.
3. Check readings in read-only mode, enable control and try your
   [first charge](docs/quickstart.md#7-first-charge).

| Connection | Why choose it? |
|---|---|
| **Service app — recommended, optional** | Keeps the heartbeat and last charging state running through HA Core restarts. On HA OS, its URL and token are discovered automatically. |
| **Direct USB or LAN** | Uses the same charging controls without an extra app. HA restarts interrupt its heartbeat. |

The service app is installed separately from the integration:

[![Add the Mennekes Amtron app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps)

On HA Container, use the [standalone service](docs/service-standalone.md) or connect
directly. Both connection owners support USB and LAN. Only one owner may use the
bus, including in read-only mode. An app/host restart or a connection failure can
still interrupt the heartbeat; prepare the wallbox's [fallback](docs/safety.md).

Already installed? **Keep the existing entry.** The integration and app update
independently and can have different version numbers. A compatible app need not
restart for an integration-only update. Follow [Updates](docs/installation.md#updates).

## Everyday charging

| I want to… | Use |
|---|---|
| Set the charging current | **Charging current limit** slider |
| Remove the current limit | **0 A**; this does not pause charging |
| Pause or resume | **Charging paused** switch |
| Check the heartbeat | **Heartbeat** diagnostic sensor |
| Track energy | **Total energy** in the Energy dashboard |
| Add targets, a trip time or vehicle data | [Optional functions and examples](docs/features.md) |
| Estimate costs or charging losses | [Charging estimates](docs/charging-estimates.md) |
| Charge from solar surplus | [PV blueprints](docs/blueprints.md) |
| Keep history manageable | [Recorder settings](docs/recorder.md) |

Write permission has one owner: disable **Read-only** in the service app, or
select **Modbus master** in the integration's direct-connection options.
[The user guide](docs/user-guide.md#where-do-i-enable-controls) explains where each
setting belongs and why a requested change may wait.

## Development and releases

**Stable: 0.6.1 · Beta: 0.8.0b4.** The beta includes vehicle profiles, optional
automatic selection and Virtual Plug & Charge, live app Modbus debugging and
guided app setup on Supervisor 2026.10.4+. Beta 2 adds a wallbox Plug & Charge
switch, guided vehicle setup and a separate app **Debug log** tab. Beta 3 lets
Plug & Charge end a manual pause for a permitted car without the state-change
delay and names the reason when charging stays blocked. Beta 4 stops entities
briefly turning unavailable, keeps the selected car after unplugging and
remembers each car's percentage target. Optional
features stay off until you configure them. In service mode, update the beta app
first through
**#apps-beta**. See [the beta update guide](docs/upgrading.md#trying-080b4),
[feature availability](docs/README.md#versions-and-feature-availability) and
[release notes](CHANGELOG.md).

[All documentation](docs/README.md) · [Compatibility](docs/compatibility.md) · [Troubleshooting](docs/troubleshooting.md) · [Report a problem](https://github.com/memphi2/ha-mennekes-amtron/issues/new/choose)

Unofficial community project, not affiliated with MENNEKES.
[Apache 2.0 license](LICENSE) · [Notices](NOTICE) · [Legal information](docs/legal.md)
