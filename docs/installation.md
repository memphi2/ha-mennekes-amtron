# Install and connect your wallbox

[Documentation](README.md) › Installation

Install the Home Assistant integration, then choose who connects to the
wallbox: the optional service app or Home Assistant itself. Normal setup on
Home Assistant OS uses the UI and needs no YAML or command line.

Already installed? Keep your existing entry and go to [Updates](#updates) or
[move the existing wallbox to the service](modbus-service.md#move-an-existing-wallbox).

## Before you start

- Home Assistant **2026.9.0 or newer** and a [supported wallbox](compatibility.md).
- A qualified electrician has prepared Modbus satellite mode, RS-485 wiring and
  the wallbox's fallback. Follow the [hardware guide](hardware.md).
- A USB RS-485 adapter or [LAN-to-RS-485 adapter](network-gateway.md).
- One software owner for that bus. The service, a direct HA connection and
  another energy manager must not use it simultaneously; read-only still sends
  requests.

Keep the vehicle disconnected during the first control setup.

## 1. Choose your connection

| | Service app — recommended, optional | Direct USB or LAN |
|---|---|---|
| Install | Integration plus service app | Integration only |
| Who uses the adapter? | Service app | Home Assistant |
| During an HA Core restart | Heartbeat and last charging state continue | Heartbeat is interrupted; wallbox fallback applies |
| Where are writes enabled? | App **Configuration → Read-only** off | Integration **Configure → Wallbox → Control mode: Modbus master** |

Both support USB and LAN. A LAN adapter needs no local USB device. On HA
Container, the service can run as a [separate container](service-standalone.md).
The service keeps a deliberate pause as well as a running charge while HA is
offline; HA automations and vehicle-based planning still need HA to run.
An app/host restart or USB/LAN failure can interrupt either setup's heartbeat.

```mermaid
flowchart LR
    HA["Home Assistant integration"] --> APP["Optional Modbus service"]
    APP --> LINK["USB or LAN adapter"] --> WB["Wallbox"]
```

Without the service, Home Assistant connects directly to that adapter.

## 2. Install the integration

### Home Assistant 2026.11 and newer

[![Open in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=memphi2&repository=ha-mennekes-amtron&category=integration)

Open **Settings → Marketplace** and install **Mennekes Amtron RTU** by
**memphi2**. Choose the stable release unless you deliberately want a beta.
Accept the community-content notice and follow any restart request.
A first Marketplace installation can load without a Core restart; replacing
already loaded code requires one.

### Home Assistant 2026.9 and 2026.10

Open **HACS**, find **Mennekes Amtron RTU** by **memphi2**, download the stable
release and restart Home Assistant. If needed, add
`https://github.com/memphi2/ha-mennekes-amtron` in **HACS → ⋮ → Custom
repositories**, category **Integration**.

**0.6.1 remains stable.** For vehicle profiles, optional automatic selection and
Virtual Plug & Charge, choose **beta 0.8.0b6** and follow the
[beta update guide](upgrading.md#trying-080b6).
[Check feature availability](README.md#versions-and-feature-availability)
before looking for a new option in an installed release.

## 3. Connect the wallbox

| Your choice | Next step |
|---|---|
| **Service app on HA OS** | [Install and configure the app](modbus-service.md#install-on-home-assistant-os), leave it read-only and confirm its discovery in HA. URL and token are supplied automatically. |
| **Standalone service** | [Start the service](service-standalone.md), then enter its service URL and token when adding the integration. |
| **Direct USB** | [Add a direct serial connection](quickstart.md#direct-serial-connection) and select the adapter. |
| **Direct LAN** | [Add a direct LAN connection](quickstart.md#direct-lan-adapter-connection) with protocol, adapter IP/hostname, TCP port and wallbox address. |

A **Service URL** points to this project's HTTP service. An adapter's **IP and
TCP port** point to the LAN-to-RS-485 hardware. They are different fields for
different connections. On HA OS, you normally type neither a service URL nor a
token for the app.

The app's **Open Web UI** provides guided connection setup. App **0.8.0b4**
supports **Supervisor 2026.10.4+**; update an older beta app if it incorrectly asks
for 2026.11.0. Native **Configuration** remains available for manual setup.
[Connection-page details](upgrading.md#app-connection-page).

## 4. Check readings and try a charge

Compare the readings with the wallbox while still read-only. Choose an
appropriate current cap, then enable writes in the single place shown in the
connection table. Follow [First charge](quickstart.md#7-first-charge) to check
current control, pause and resume. Verify **Heartbeat** and resolve connection
repairs before adding automation.

Basic charging is now ready. [Optional functions](features.md) covers vehicle
sources, one or several cars, targets, calendars, costs and losses. None is
required just to use the wallbox.

## Updates

1. Back up HA and read the release notes. Keep the existing integration entry.
2. Update the app **only if an app update is offered or the integration requires
   a newer service**. If both need updating, update the app/service first and
   wait for it to initialize. Its restart briefly interrupts the heartbeat.
3. Update the integration through Marketplace/Settings → Updates or HACS, then
   follow the required HA Core restart.
4. Check current readings, heartbeat, pause/release and the intended current.

The app and integration may have different version numbers. Stable 0.6.1 and
beta 0.8.0b4 require **service 0.6.1+ with API 2** for ordinary service connections.
A compatible app can keep running during an integration-only update. To use
Virtual Plug & Charge in service mode, use **app 0.8.0b1 or newer**. Update to
**app 0.8.0b2** for the separate **Debug log** tab. When updating both components,
update the app first.

Use **Check for updates** in the app store if an offered app update is missing.
Moving between stable and beta app catalogs creates a separate app and requires
[the channel handover](modbus-service.md#updates). Moving from HACS to Marketplace
does not require adding the wallbox again. For changed blueprints,
[update the existing automation](blueprints.md#updating-existing-blueprint-automations).

## Updating to 0.6.1

When coming from an older stable or beta, update the **app/service to 0.6.1 or
newer before the integration**. Keep its data and existing HA entry. This provides
expiry for queued departure commands while HA is offline. Follow the
[version-specific update guide](upgrading.md#updating-to-061), including old app
connection-choice changes when relevant.

## Change settings later

| Setting | Where to change it |
|---|---|
| Direct USB/LAN connection | Integration entry **Reconfigure** |
| App USB/LAN connection | App **Open Web UI**, or native Configuration where needed |
| App write permission, current cap and bus polling | App **Configuration**, then save and restart the app |
| HA options and optional vehicle features | Integration **Configure**, in the relevant section |

Changing app settings requires an app restart; HA then receives the new status
without an additional Core restart. Use [the settings map](user-guide.md#configuration-and-options)
for the full list. Keep device/entity IDs when changing connection; do not
remove and re-add the wallbox.

## Removal

1. Stop charging and disable automations that control the wallbox. Check its
   fallback before stopping the heartbeat.
2. Remove the intended entry under **Settings → Devices & services**. A direct
   entry releases the adapter. A service entry only disconnects HA: **the app
   keeps running** until you stop it separately.
3. Uninstall the integration from the Marketplace or HACS once no remaining
   entry uses it, then follow any restart request.

Removal does not reset wallbox settings or delete Recorder history and
blueprints. Update dashboards and automations that used the removed entities.
Stop the app separately before giving the adapter to another bus owner.

---

[Everyday use](user-guide.md) · [Troubleshooting](troubleshooting.md) · [Keep history manageable](recorder.md)
