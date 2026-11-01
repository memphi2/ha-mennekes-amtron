# Installation and updates

Install **MENNEKES AMTRON RTU (Unofficial)** from
**memphi2/ha-mennekes-amtron**. This is a community integration for Modbus RTU
wallboxes, using the domain `mennekes_amtron_rtu`.

## Requirements

- Home Assistant **2026.9.0** or newer. Use the Marketplace on **2026.11+**,
  or HACS on **2026.9/2026.10**.
- A USB RS-485 adapter available to Home Assistant, preferably through a
  stable `/dev/serial/by-id/` path.
- A wallbox configured as a Modbus satellite and exclusive access to its
  Modbus RTU bus. Read-only mode also sends requests.

Follow [hardware installation](hardware.md) before configuring the wallbox.
Use a published release for normal operation. The [support policy](../SUPPORT.md)
distinguishes stable support from November beta validation.

**Upgrading from 0.1.0:** follow the [domain migration guide](upgrading.md)
first. The steps for keeping an existing entry below apply to **0.2.x**.

## Home Assistant Marketplace

For Home Assistant 2026.11 and newer:

1. Open **Settings → Marketplace** and accept the community-content notice
   when requested.
2. Search for **MENNEKES AMTRON RTU (Unofficial)** by **memphi2**.
3. If it is not listed, open the repository using the button below. While
   [catalog PR #11749](https://github.com/hacs/default/pull/11749) is pending,
   add it as a custom repository, category **Integration**. Custom repositories
   require a GitHub connection in the Marketplace.
4. Install the integration.
5. Open **Settings → Devices & services → Add integration** and select
   **MENNEKES AMTRON RTU (Unofficial)**. Follow the
   [wallbox setup](quickstart.md#5-add-the-wallbox).

[![Open in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=memphi2&repository=ha-mennekes-amtron&category=integration)

Repository URL: `https://github.com/memphi2/ha-mennekes-amtron`.

A first Marketplace installation normally makes the config flow available
without a restart. Updating or removing integration code that Home Assistant
has already loaded requires a restart; follow the restart request. Copying or
downloading new files does not replace code already running in memory.

See the official [Marketplace guide](https://contributors.home-assistant.io/marketplace/how-it-works/)
and [installation behavior](https://contributors.home-assistant.io/marketplace/guides/integrations/).

## HACS on Home Assistant 2026.9 and 2026.10

1. Open HACS and search for **MENNEKES AMTRON RTU (Unofficial)** by **memphi2**.
2. If it is not listed, open the three-dot menu → **Custom repositories**.
   Add `https://github.com/memphi2/ha-mennekes-amtron` with category **Integration**.
3. Download the integration and restart Home Assistant.
4. Add **MENNEKES AMTRON RTU (Unofficial)** under **Settings → Devices & services**
   and follow the [wallbox setup](quickstart.md#5-add-the-wallbox).

## Existing installations

For **0.2.x**, keep your config entries, devices, entity IDs and automations.
Moving from HACS to the Marketplace does not change this integration's
repository or domain and does not require adding the wallbox again.

When upgrading Home Assistant to 2026.11, the Marketplace takes over existing
HACS-managed installations and custom repositories. Do not install a second
copy or reinstall HACS on 2026.11. The older **0.1.0 → 0.2.x** domain change is
separate and still requires the [migration steps](upgrading.md).

If the serial port or bus settings change, use **Reconfigure** on the existing
entry. For polling intervals and control mode, use its options.

## Updates

1. Back up Home Assistant and read the offered release notes.
2. Account for the heartbeat interruption before restarting: master mode
   stops sending it while HA is down. See [fallback behavior](safety.md).
3. On 2026.11+, install the update under **Settings → Updates**. On older
   supported Home Assistant versions, update through HACS.
4. Complete the requested Home Assistant restart.
5. Check that the existing wallbox entry loads and reports current readings.
   In master mode, also check for energy-manager or fallback repair messages.
6. Review blueprint automations for changed inputs. The upcoming PV fix needs
   the new **Charging paused** input on existing current automations; follow
   [Blueprint updates](blueprints.md#updating-existing-blueprint-automations).

The Marketplace checks the minimum Home Assistant version for each release.
Catalog changes can take several hours to appear. Keep the existing entry
while waiting; reinstalling it is not needed. See the
[release delivery guide](https://contributors.home-assistant.io/marketplace/guides/releases/).

## Removal

1. Stop charging and disable automations that control this wallbox. Check the
   configured energy-manager fallback before stopping its heartbeat; see
   [safety and limitations](safety.md).
2. Under **Settings → Devices & services**, delete the AMTRON RTU entries you
   intend to remove. This stops their heartbeat, closes the port and removes
   their entities. Update dashboards and automations that used them.
3. To remove the integration files too, uninstall **memphi2/ha-mennekes-amtron**
   from the Marketplace, or from HACS on older Home Assistant versions. Keep
   the files while other AMTRON RTU entries still use them.
4. Complete the requested Home Assistant restart.

An entry removal does not reset wallbox registers. The wallbox applies its
configured fallback when the heartbeat expires. Removing integration files
also leaves installed blueprints and Recorder history in place; remove
blueprint copies only after no automation uses them.

## Help

- [Quick start](quickstart.md): adapter, bus check, configuration and first charge.
- [Troubleshooting](troubleshooting.md): communication and error recovery.
- [Recorder](recorder.md): history retention and optional statistics exclusions.
