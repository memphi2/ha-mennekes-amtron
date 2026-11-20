# Mennekes Amtron RTU

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Marketplace](https://img.shields.io/badge/Home_Assistant-Marketplace-41BDF5?style=flat-square)](https://contributors.home-assistant.io/marketplace/)
[![Quality: Platinum (self-assessed)](https://img.shields.io/badge/Quality-Platinum%20%28self--assessed%29-c8cdd3?labelColor=3d4852)](docs/quality-scale.md)

Control your Mennekes Amtron in Home Assistant: set the charging current, pause
charging, track energy and add solar, vehicle or departure-based charging when
you need it. Communication stays local through **USB/RS-485 or a LAN adapter**.

**[Install](docs/installation.md)** · **[Everyday use](docs/user-guide.md)** · **[Dashboard cards](docs/app-from-home-assistant.md#dashboard-with-mushroom-cards)** · **[Get help](docs/troubleshooting.md)**

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
   or a **[direct USB/LAN connection](docs/installation.md#3-connect-the-wallbox)**.
3. Check readings in read-only mode, enable control and try your
   [first charge](docs/quickstart.md#first-charge).

| Connection | Why choose it? |
|---|---|
| **Service app — recommended, optional** | Keeps the heartbeat, last charging state, shared limits and accepted vehicle plans running through HA Core restarts. On HA OS, its URL and token are discovered automatically. |
| **Direct USB or LAN** | The same features without an extra app: Home Assistant runs the same wallbox service itself. HA restarts interrupt its heartbeat. |

The service app is installed separately from the integration:

[![Add the Mennekes Amtron app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps)

On HA Container, use the [standalone service](docs/service-standalone.md) or connect
directly. Both connection owners support USB and LAN. Only one owner may use the
bus, including in read-only mode. An app/host restart or a connection failure can
still interrupt the heartbeat; prepare the wallbox's [fallback](docs/safety.md).

Already installed? **Keep the existing entry** and follow
[Updates](docs/installation.md#updates): update the app first, then the integration.

## Use it every day

Set **Charging current limit** and use **Charging paused** to stop or resume.
**0 A removes the current limit; it is not a pause.** Add an energy target, cost target or optional
vehicle charge target when needed; setting a target does not start charging.

**Start guest charging** releases the connected car now,
optionally until the first reached energy target, cost target or target time. No guest order needs
to be created. Intelligent vehicle planning is optional and configured in HA.

| You want to… | Open |
|---|---|
| Learn the controls, waits and restart behavior | [Everyday charging](docs/user-guide.md) |
| Add a card without building helpers | [Basic and Mushroom YAML in five languages](docs/app-from-home-assistant.md#dashboard-with-mushroom-cards) |
| Use the app's browser interface | [App guide](docs/app-guide.md) |
| Use one or several cars, percentage limits or Plug & Charge | [Vehicle setup](docs/vehicle-setup.md) |
| Have the car ready for the next trip | [Departure planning](docs/departure-planning.md) |
| Add costs, losses or solar charging | [Optional functions](docs/features.md) |
| Keep the database manageable | [Recorder settings](docs/recorder.md) |
| Resolve a connection or charging problem | [Troubleshooting](docs/troubleshooting.md) |

## Support the project

Every contribution is voluntary and very welcome. Support and new features
stay open to everyone either way.

<p align="center">
  <a href="https://ko-fi.com/oliver7557"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi"></a>
</p>

## Guides and updates

**[Guide home](docs/README.md)** · **[Release notes](CHANGELOG.md)** · **[Report a problem](https://github.com/memphi2/ha-mennekes-amtron/issues/new/choose)**

Integration and app versions are independent; update the app first
when a newer minimum is required. Only a changed app needs an app update.

Unofficial community project, not affiliated with MENNEKES. Provided as is,
without warranty (Apache-2.0 sections 7 and 8); use at your own risk.
[Apache 2.0 license](LICENSE) · [Notices](NOTICE) · [Legal information](docs/legal.md)
