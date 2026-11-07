# Installation and updates

[Start](../README.md) › Installation

For a new installation, install the integration and choose how it connects to the wallbox.
On Home Assistant OS, everything is configured through the UI. Normal setup
needs no YAML and no command-line tools.

Already installed? Go to [Updates](#updates) or
[switch an existing installation to the service](modbus-service.md).
Keep your existing entry, devices and automations.

## Before you start

- Home Assistant **2026.9.0 or newer**.
- A [supported wallbox](compatibility.md) prepared for Modbus RTU by a qualified
  electrician, including its heartbeat fallback settings.
- A USB RS-485 adapter or [LAN-to-RS-485 gateway](network-gateway.md), with
  **exclusive access to the bus**. The integration,
  service and another energy manager must never use it at the same time.

Follow the [hardware guide](hardware.md) for wiring, wallbox settings and USB
passthrough. Leave the vehicle disconnected during the first control setup.

## 1. Choose your connection

| | Independent service | Direct USB or LAN |
|---|---|---|
| Best fit | **Recommended** for a heartbeat that survives HA Core restarts | An installation without an extra app |
| What to install | Integration + service app on HA OS, or a separate service container | Integration only |
| USB adapter or LAN gateway is used by | Service | Home Assistant |
| Enable writing in | App configuration → **Read-only** off | Integration options → **Control mode: Modbus master** |
| During a Core restart | Last charging state and heartbeat continue | Heartbeat is interrupted; wallbox fallback applies |

Both connection owners support USB and LAN; see
[LAN connection setup](network-gateway.md). Compatible Waveshare adapters can
also use the optional [LAN adapter configuration](lan-adapters.md). A LAN connection
needs no local USB device, and does not change the supported RTU wallbox models.

The service keeps an intentional pause, disabled charging release and selected
current even if HA stays offline. HA automations cannot make new decisions while
HA is down. Restarting the app or host, or losing USB, can still interrupt the
heartbeat, as can a gateway or network outage. See
[fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).

With the service, the connection looks like this:

```mermaid
flowchart LR
    HA["Home Assistant"] --> APP["Modbus service"]
    APP --> LINK["USB or LAN / RS-485"] --> WB["Mennekes Amtron"]
```

The service continues running when the Home Assistant part restarts.

## 2. Install the integration

### Home Assistant 2026.11 and newer

[![Open in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=memphi2&repository=ha-mennekes-amtron&category=integration)

1. Open the repository with the button, or search **Settings → Marketplace**
   for **Mennekes Amtron RTU** by **memphi2**.
2. Accept the community-content notice when prompted.
3. Select stable **0.6.1**.
4. Install the integration and follow any restart request.

The first Marketplace installation can be used without a Core restart.
Replacing code already loaded by HA needs a restart; see
[Home Assistant's installation behavior](https://contributors.home-assistant.io/marketplace/guides/integrations/#restarts).

### Home Assistant 2026.9 and 2026.10

1. Open **HACS** and search for **Mennekes Amtron RTU** by **memphi2**.
2. Select stable **v0.6.1**.
3. Download the integration and restart Home Assistant.

If the repository is missing from the catalog, add
`https://github.com/memphi2/ha-mennekes-amtron` through
**HACS → ⋮ → Custom repositories**, category **Integration**.

## 3. Connect the wallbox

| Your choice | Continue here |
|---|---|
| **Service on HA OS** | [Install the app](modbus-service.md), start it read-only and confirm the discovered integration. URL and token are supplied automatically. |
| **Service with HA Container** | [Run the standalone service](service-standalone.md), then enter its URL and token in the integration. |
| **Direct serial** | [Add the wallbox](quickstart.md#5-add-the-wallbox): choose **Direct serial connection**, then select the adapter and bus settings. |
| **Direct LAN adapter** | [LAN setup](network-gateway.md): choose **Direct LAN adapter connection**, then select the adapter protocol, host, TCP port and wallbox address. |

Start by comparing the readings with the wallbox. Then set an appropriate
current cap and enable writing in the place shown above. Test charging, pause
and resume before adding [automations](blueprints.md).

The service is a separate app: installing the integration from the Marketplace
or HACS does not install it. It remains optional, and choosing it never deletes
your existing device or entity IDs.

The updated app includes an **Open Web UI** page for USB/LAN
setup. That page requires an administrator and stable **Supervisor 2026.10.4+**;
it does not raise the integration's Home Assistant minimum version. See
[the connection page](upgrading.md#app-connection-page) for setup and updates.

## Updates

1. Back up Home Assistant and read the integration's release notes.
2. **Update the app only when an app update is offered or the integration needs
   a newer service.** If both need updating, update the app or standalone service
   first, keep its data and wait for initialization. Its restart briefly
   interrupts the heartbeat.
3. Update the integration under **Settings → Updates** on HA 2026.11+, or through
   HACS on older supported versions, then restart Home Assistant Core.
4. Confirm current readings, the intended pause/release settings and heartbeat
   health. Keep the existing integration entry.

**The integration and app may have different version numbers.** Integration
**0.6.1** requires **service 0.6.1 or newer with API 2**. Update an older service
first. This supplies expiry for queued timed-plan commands
even while HA is offline. Future integration-only updates can reuse a compatible
app without restarting it; its heartbeat continues through HA Core restarts.

For older app versions and saved connection choices, follow
[Updating an existing installation](upgrading.md). Keep the existing entry and app data.

Updates can take time to appear; use **Check for updates** in the app store for
app updates. See
[Home Assistant's release guidance](https://contributors.home-assistant.io/marketplace/guides/releases/#pre-releases).

Moving from HACS to the Marketplace does not require a second installation or
adding the wallbox again; [HA carries over the existing repositories](https://contributors.home-assistant.io/marketplace/how-it-works/#before-home-assistant-202611).
For blueprint changes, follow
[Updating existing automations](blueprints.md#updating-existing-blueprint-automations).

## Updating to 0.6.1

**0.6.1 is a stable release of the integration and app**, including the features
previously offered in 0.5.2 and 0.6.0 betas. Keep your existing HA entry and back
up the app's data before updating. Service mode requires **app/service 0.6.1 or
newer**; update it before the integration, even if you do not use timed planning.

- **Marketplace:** install the stable **0.6.1** update. No prerelease switch is
  needed for this version.
- **HACS:** select **v0.6.1**. Existing beta installations can move to this stable
  version without adding the integration again.
- **App:** `https://github.com/memphi2/ha-mennekes-amtron#apps` offers stable
  **0.6.1**. Use **Check for updates** and keep the existing app data.
  The `#apps-beta` catalog may offer a newer prerelease; see
  [trying 0.7.0b4](upgrading.md#trying-070b4). A catalog switch creates a separate
  app; follow the [channel handover](modbus-service.md#updates).

Before updating the app from 0.5.1, save one of the three readable connection
choices if a technical value such as `serial` is still selected; see
[App connection choice](upgrading.md#app-connection-choice). Update the app
first, wait for initialization, then update the integration. Check charging,
pause, current limiting and heartbeat on your hardware after the update.

## Change settings later

Open **Configure → Wallbox** for the integration's operating settings.
**Vehicle charge target**, **Next departure**, **Charging costs** and **Charging
losses** are separate optional sections; see [feature setup](user-guide.md#configuration-and-options).

- **Direct serial:** use **Reconfigure** for the adapter and bus settings;
  use the integration options for write permission, intervals and current cap.
- **Direct LAN:** use **Reconfigure** for protocol, host, TCP port and wallbox
  address; the integration options control writes, intervals and current cap.
  Set serial baud rate/frame on the adapter itself, or use optional
  [Set up LAN adapter](lan-adapters.md) for a recognized model.
- **Service:** use **Open Web UI** for USB/LAN connection settings and the native
  Configuration tab for write permission, limits and measurement interval.
  The integration options contain the HA
  status interval and an additional current cap.

Save app configuration changes and restart the app to apply them. HA picks up
its new write permission on the next status refresh; no HA restart is needed
for this switch. The app restart itself briefly interrupts the heartbeat.

See [Which setting goes where?](user-guide.md) for the everyday controls.

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
