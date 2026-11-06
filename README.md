# Mennekes Amtron RTU

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Marketplace](https://img.shields.io/badge/Home_Assistant-Marketplace-41BDF5?style=flat-square)](https://contributors.home-assistant.io/marketplace/)
[![Quality: Platinum (self-assessed)](https://img.shields.io/badge/Quality-Platinum%20%28self--assessed%29-c8cdd3?labelColor=3d4852)](docs/quality-scale.md)

Your Mennekes Amtron in Home Assistant: adjust the charging current, pause
charging, follow energy use and charge from your solar surplus. Communication
stays local over **Modbus RTU / RS-485**, through USB or a
[LAN-to-RS-485 adapter](docs/network-gateway.md).

**[Get started](docs/installation.md)** · **[Everyday use](docs/user-guide.md)** · **[Get help](docs/troubleshooting.md)**

Integration and app available in **English, German, French, Dutch and Italian**.

## Supported wallboxes

- **Amtron 4You 300**
- **Amtron Compact 2.0s**
- **Amtron Start 2.0s**

See the [compatibility matrix](docs/compatibility.md) for individual models
and hardware-test status.

## Choose how to connect

| Connection | What you get |
|---|---|
| **Independent Modbus service — recommended, optional** | The app keeps the heartbeat and last charging state through Home Assistant Core restarts. HA discovers the app automatically on HA OS. |
| **Direct Home Assistant connection** | No extra app; Home Assistant owns the connection and its heartbeat stops while HA restarts. |

Both support **USB and LAN** from **0.5.0**. LAN adapters can use transparent
RTU-over-TCP or a translating Modbus TCP-to-RTU gateway; see [LAN setup](docs/network-gateway.md).

The service is our preferred setup for everyday use. Direct mode remains
supported; existing installations are never switched automatically.
Both offer the same charging controls.
A host or service restart can still interrupt the heartbeat.

## Install

[![Open in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=memphi2&repository=ha-mennekes-amtron&category=integration)

1. Install **Mennekes Amtron RTU** by **memphi2**. Use the Marketplace on
   HA **2026.11+**, or HACS on **2026.9/2026.10**.
2. For the service, follow the
   **[app installation guide](docs/modbus-service.md)**. The app supplies its
   address and token automatically on HA OS. For a direct connection, follow
   **[Quick start](docs/quickstart.md)** or **[LAN setup](docs/network-gateway.md)**.
3. Check the readings, enable control and try your first charge.

[![Add the Mennekes Amtron app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps)

The integration and service app are installed and updated independently.
A normal integration update leaves the compatible app and its heartbeat running;
matching version numbers are not required. See [Updates](docs/installation.md#updates).
On HA Container, run the service as a [separate container](docs/service-standalone.md).
Already using the integration? **Keep your existing entry** and follow
[Updates and switching connections](docs/upgrading.md).

**Stable 0.6.1** adds next-trip charging by manual time, calendar or optional
BavarianData timer, with a preconditioning blueprint and German Module 3 tariff
import with yearly updates. In service mode, update the **app to 0.6.1 first**;
it expires obsolete queued departure commands even while HA is offline.
See [how to update](docs/installation.md#updating-to-061) and
[what changes](docs/upgrading.md#updating-to-061).

You need a compatible wallbox, a USB RS-485 adapter or supported LAN gateway,
and exclusive access to its bus. LAN needs no USB device on the HA/service host.
Have the [hardware and fallback settings](docs/hardware.md) prepared before
enabling control. Read-only mode also needs exclusive bus access.

## Ready for everyday use

Charging targets, departure planning and estimates are optional features of **0.6.1**.

| I want to… | Use |
|---|---|
| Adjust the charging current | **Charging current limit** slider: 6 A up to the configured cap |
| Remove the current limit | Set the slider to **0 A**; the wallbox's hardware limit still applies |
| Pause or resume charging | **Charging paused** switch; 0 A does **not** pause charging |
| See whether the heartbeat is working | **Heartbeat** diagnostic sensor |
| Track consumption | **Total energy** in the Energy dashboard |
| Limit charging | **Energy target**, or an optional **Vehicle charge target** from your car's SoC sensor |
| Estimate losses and costs | [Optional charging estimates](docs/charging-estimates.md) using existing vehicle and price data |
| Charge from solar surplus | [Ready-made PV blueprints](docs/blueprints.md) |
| Charge for the next trip | [Next departure](docs/user-guide.md#charge-for-the-next-departure), with optional calendar, BMW timer and climate automation |
| Keep history manageable | [Recorder settings](docs/recorder.md); the heartbeat records status changes, not a timestamp every five seconds |

**Where do I enable control?** With the service, turn off **Read-only** in the
app configuration, save and restart the app. HA controls follow automatically. With
direct USB or LAN mode, select **Modbus master** in the integration options.

## Find your way

- [Installation](docs/installation.md) · [User guide](docs/user-guide.md) · [All entities](docs/entities.md)
- [Troubleshooting](docs/troubleshooting.md) · [Safety and limitations](docs/safety.md)
- [Optional charging losses and costs](docs/charging-estimates.md)
- [All documentation](docs/README.md) · [Release notes](CHANGELOG.md) · [Report a problem](https://github.com/memphi2/ha-mennekes-amtron/issues/new/choose)

Unofficial community project, not affiliated with MENNEKES.
[Apache 2.0 license](LICENSE) · [Notices](NOTICE) · [Legal information](docs/legal.md)
