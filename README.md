# Mennekes Amtron RTU

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Local Home Assistant integration for Mennekes Amtron wallboxes over
**Modbus RTU / RS-485**. Monitor charging, power and energy, adjust the
charging current, pause charging and use PV surplus automations.

## Supported device families

- **Amtron 4You 300**
- **Amtron Compact 2.0s**
- **Amtron Start 2.0s**

See the [compatibility matrix](docs/compatibility.md) for individual models
and hardware-test status.

## Installation

For **Home Assistant 2026.11 and newer**:

[![Open your Home Assistant instance and open this repository in the Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=memphi2&repository=ha-mennekes-amtron&category=integration)

1. Open the repository using the button above, or search the Marketplace for
   **Mennekes Amtron RTU** by **memphi2**.
2. Install **Mennekes Amtron RTU** and accept the community-content notice.
3. Under **Settings → Devices & services → Add integration**, select
   **Mennekes Amtron RTU** and choose the connection to your wallbox.
   Follow the [quick start](docs/quickstart.md) for setup and the first charge.

Use an **exclusive RS-485 bus**, including in read-only mode. Start read-only
and verify the readings; before enabling control, configure the wallbox's
heartbeat fallback. Have electrical work carried out by a qualified electrician.
See [hardware setup](docs/hardware.md) and [safety](docs/safety.md).

For HACS on HA 2026.9/2026.10, updates or an existing installation, see
[Installation and updates](docs/installation.md).

## Everyday use

| Task | Control or guide |
|---|---|
| Set the charging current | **Charging current limit**: 0 A disables the limit; 6 A up to the configured cap sets a limit |
| Pause or resume charging | **Charging paused**; resume follows the required waiting interval |
| Track consumption | **Total energy** in the Energy dashboard |
| Charge from PV surplus | [PV blueprints](docs/blueprints.md) |
| Limit database growth | Keep the 30-second measurement default and choose a [Recorder profile](docs/recorder.md) |

Controls are available in **Modbus master** mode. **0 A removes the current
limit**; use **Charging paused** to stop charging.

## Keep the heartbeat running during HA restarts

The optional [Modbus service](docs/modbus-service.md) runs independently of HA
and keeps the last charging state, including a deliberate pause. It is available
in **0.5.0b3**, a beta for installation and hardware testing. Enable
**Pre-release** for this repository in the Marketplace, or beta releases in HACS,
and install the matching integration version before switching over.

[![Add the Mennekes Amtron beta app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps-beta)

On HA OS, install the service from this app repository. The app is discovered
automatically in **Devices & services**, including its address and access token.
HACS and the Marketplace
install the integration; the app is installed separately. For HA Container,
use the published Docker image. Follow the [handover guide](docs/modbus-service.md)
to retain existing device and entity IDs. **0.4.1 remains the stable release.**

## Documentation and help

- [User guide](docs/user-guide.md) · [Entity reference](docs/entities.md)
- [Automations](docs/automations.md) · [Blueprints](docs/blueprints.md)
- [Troubleshooting](docs/troubleshooting.md) · [Report a problem](https://github.com/memphi2/ha-mennekes-amtron/issues/new/choose)
- [All documentation](docs/README.md) · [Release notes](CHANGELOG.md)

Unofficial community project, not affiliated with MENNEKES.
[Apache 2.0 license](LICENSE) · [Notices](NOTICE) · [Legal information](docs/legal.md)
