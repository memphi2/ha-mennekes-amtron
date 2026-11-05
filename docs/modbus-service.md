# Modbus service: the preferred setup

The optional **Mennekes Amtron Modbus Service** keeps the wallbox connected while
Home Assistant Core restarts. It is the preferred setup when you want the
heartbeat and your chosen charging state to continue independently of HA.
Direct USB and LAN connections remain available without the extra service.

The service can reach the RTU wallbox through **USB or a LAN-to-RS-485 adapter**.
LAN needs no USB device on its host; see [LAN setup](network-gateway.md) for the
two supported gateway protocols.

**Current service release: 0.5.1.** The integration and app are installed and
updated independently. Their version numbers do not need to match.
Check the [handover](#check-the-handover) on your hardware before relying on it.

[Install on HA OS](#install-on-home-assistant-os) ·
[Move an existing wallbox](#move-an-existing-wallbox) ·
[Everyday use](#everyday-use) · [Updates](#updates) · [Help](#if-something-is-wrong)

| Your installation | How to run the service |
|---|---|
| Home Assistant OS | Install the app using the steps below. Address and token are discovered automatically. |
| Home Assistant Container or another host | Follow the [standalone Docker/Python guide](service-standalone.md). |

## Move an existing wallbox

Keep your existing integration entry: the service connection reuses the device
and entity IDs. Before starting the app:

1. Note the pause state, charging-current limit, charging release and the current
   you want to resume at if paused.
2. Disconnect the vehicle for the first handover. The heartbeat is interrupted
   while the USB or LAN connection changes owners, so the wallbox may apply its fallback.
3. **Disable the existing direct USB or LAN integration entry. Do not delete it.**
   This releases its USB or gateway connection for the app.

Continue below. For a new installation, start directly with the installation
steps. In either case, the app must be the only Modbus master on the bus;
read-only mode also sends requests. Check the [wiring and bus settings](hardware.md)
if the wallbox has not yet been prepared.

## Install on Home Assistant OS

### 1. Install the integration

Install **Mennekes Amtron RTU** from the Marketplace on HA 2026.11+, or
HACS on HA 2026.9/2026.10, and complete the requested Core restart.
Use the stable release; pre-release access is not required.

Already using the service? Go to [Updates](#updates); keep your existing entry.

### 2. Install the app

[![Add the Mennekes Amtron app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps)

Or add this repository under **Settings → Apps → Install app → ⋮ → Repositories**:

```text
https://github.com/memphi2/ha-mennekes-amtron#apps
```

Install **Mennekes Amtron Modbus Service**. During installation, Supervisor may
show **Building**. Enable **Start on boot** and **Watchdog**.

### 3. Configure and start read-only

If another integration owns the USB or LAN connection, complete the
[handover preparation](#move-an-existing-wallbox) before starting the app.

In app **0.5.1**, **Adapter host / IP** is always visible. Enter the adapter's
hostname or IP for LAN; leave it empty for USB. If **Serial adapter** is hidden,
enable **Show unused optional configuration options** in the Configuration tab.
Changing the connection choice does not hide or reveal other fields.

| App option | What to enter |
|---|---|
| Wallbox connection | **USB / RS-485** (default), **LAN: Modbus RTU (TCP)** for a transparent adapter, or **LAN: Modbus TCP** for a translating gateway. |
| Serial adapter | USB only: select the adapter, preferably `/dev/serial/by-id/...`. Leave unset for LAN. |
| Adapter host / IP + TCP port | LAN: gateway hostname/IP and listening port, default **502**. No URL scheme or path. For USB, leave the host empty. |
| Wallbox address | The configured address; factory default **50**. |
| Baud rate / serial frame | USB only: match the wallbox; factory defaults **57600 / 8n2**. For LAN, configure these on the gateway itself. |
| Read-only | Leave **on** while checking the readings. |
| Current cap | The permitted charging current for your installation; default **6 A**. |
| Status / measurement interval | Keep **5 / 30 seconds** initially. The measurement interval helps limit database traffic. |

Existing 0.5.0 settings named `serial`, `rtu_over_tcp` or `modbus_tcp` remain
valid and also appear at the end of the list; changing to their readable
counterparts is optional. No connection is changed automatically.
Still on app 0.5.0 and missing the host field? See the
[0.5.0 configuration workaround](network-gateway.md#app-050-configuration).

Leave the app's **API** listen address and port at their defaults. Its HTTP port
**8765** is separate from the gateway's Modbus TCP port. No service URL, token or
published host port is needed for the HA OS app. Start it and check its **Log**
for the wallbox connection. Read-only mode deliberately leaves the heartbeat off.

### 4. Connect Home Assistant

Open **Settings → Devices & services** and configure the discovered
**Mennekes Amtron** entry. The app supplies its address and token automatically.
An existing entry for the same wallbox is reused; re-enable it if it remains
disabled after the handover. Keep the app **Read-only** while re-enabling the
entry and checking the readings.

If no discovery card is visible, choose **Add integration → Mennekes Amtron RTU →
Connect to the Modbus service**. This also looks for the running app. If several
apps are announced, use the discovery card for the one you installed.

Compare the readings with the wallbox. The controls are present but unavailable
while **Read-only** is on.

### 5. Enable controls

Turn **Read-only off in the app**, save and restart the app. Configuration is
read at startup. Once the app is ready and HA receives fresh status, the controls
enable automatically. **There is no second write-permission switch in HA and no
HA restart is needed for this change.** An app restart briefly interrupts the
heartbeat.

Check the app's current cap. In the integration options you can set an additional
HA current cap, which can further restrict limited-current commands.
Reapply the pause, current and charging-release settings you noted before the
handover, and verify them before reconnecting the vehicle. On its first start,
the app reads the wallbox's current state; it cannot recover a previous intention
if fallback changed that state. A paused wallbox without saved app state uses
**6 A** as its first resume target.

### Check the handover

Check readings and controls, then deliberately pause charging and restart
**Home Assistant Core**. The app should remain running, its heartbeat should
stay healthy and the pause should remain active. Check normal charging and
reconnection as well before relying on the setup unattended.

A host or app restart is a different test: it interrupts the heartbeat.
Error-200 recovery while deliberately paused still needs confirmation on the
actual wallbox firmware. See [heartbeat troubleshooting](troubleshooting.md#error-200-energy-manager-unavailable).

## Everyday use

| What you want to do | Where to do it |
|---|---|
| Pause or resume charging | Use **Charging paused** in HA. |
| Set a charging-current limit | Use the current slider in HA. **0 A removes the EMS limit; it does not pause charging.** |
| Enable or disable all writes | Change **Read-only**, save and restart the app. HA needs no restart. Read-only stops the heartbeat as well as writes. |
| Change Modbus polling intervals or the service current cap | Change the app configuration, save and restart the app. |
| Change USB/LAN connection settings | Open the app configuration, save and restart the app. For LAN, serial baud/frame belong on the gateway. |
| Change how often HA reads app status, or lower HA's current cap | Open the integration options. |
| Hand the USB or LAN connection to another master | Stop the app. Disabling its HA entry or enabling read-only does not release the bus. |

Control entities keep their IDs when permission changes. They are unavailable
while the service is read-only, disconnected or not ready. HA does not send old
charging commands when permission changes or it reconnects. The service may
still recover error 200 automatically by reasserting its saved charging state,
including a deliberate pause. Restarting the app briefly interrupts its heartbeat.

The app and HA caps both constrain limited-current commands, including resuming
a saved limited setpoint. A deliberately selected **0 A** remains unlimited;
the wallbox's hardware limits still apply. See the [user guide](user-guide.md)
for charging controls and automations.

### What continues during an outage?

| Event | What happens |
|---|---|
| HA Core restarts or stays offline | The running app keeps the heartbeat and last charging state indefinitely, including a pause or disabled charging release. |
| HA automation is unavailable | No new schedule or surplus calculation runs; the app holds the last chosen state. |
| App, host, power, USB or LAN/gateway connection fails | The heartbeat can stop. The wallbox's configured fallback applies. |
| HA reconnects | HA reads the current app state; it does not restore old commands. |

Configure and understand the wallbox's [fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).
The service does not remove the need for it. Include app data in backups:
it contains saved charging intent and the private access token.

The app saves changed settings, not a growing history. Heartbeat history records
health changes rather than every beat. For smaller HA history, use a
[Recorder profile](recorder.md).

## Updates

**You do not need an app update for every integration update.** The current
integration accepts **service 0.5.0 or newer with API 2**. The released 0.5.0 app
already satisfies that requirement, even when the integration has a newer
version number. Updating only the integration and restarting Core leaves the
app, its heartbeat and its saved charging intent running with writes enabled.

When an app update is offered, read its own changes and update normally. If an
integration update requires a newer service, its release notes state that
minimum. **If both need updating, update the app first**, wait for initialization,
then update the integration and restart Core. Use **Check for updates** in the
app store; for a separate service, follow the
[standalone update steps](service-standalone.md#operation-and-updates).
**Updating the app itself briefly interrupts its heartbeat.**

**From a 0.5.0 beta:** update the app/service to stable **0.5.1 first**, then the
integration. The old beta integration may show unavailable controls between the
updates because its API differs. Keep the existing HA entry and app. Stable app
releases are offered in both `#apps` and `#apps-beta`, so no reinstall or
repository change is needed. The existing token and saved charging state are
retained. USB settings remain valid; LAN is an optional connection change.

The beta and stable repositories end in `#apps-beta` and `#apps`. The beta catalog
also offers future prereleases; the stable catalog offers stable releases only.
An optional switch to the stable-only catalog creates a different app in Supervisor.
For that channel change, back up app data, note the charging settings and
disconnect the vehicle. Copy the options, set the new app to **Read-only**, stop
the old app before starting the new one, and confirm its discovery.
The new app has separate saved state: record and verify pause, current and
charging-release settings as in the [first handover](#move-an-existing-wallbox).
Keep the old app and backup until the handover is verified.

## If something is wrong

| Symptom | First check |
|---|---|
| HA asks for a URL or token | Confirm that the app is running, then use its discovery card or **Connect to the Modbus service**. Manual credentials are for standalone services. |
| App not ready | Authentication succeeded, but wallbox data is unavailable. Check the app log for USB/LAN connection and initialization errors, then retry. No token change is needed. |
| Token rejected | During setup, retry discovery confirmation. For an existing connection, HA tries to refresh access automatically; if an authentication notice remains, [renew service access](troubleshooting.md#renew-service-access). No manual app token is needed. |
| Service API or minimum version is incompatible | Check the integration’s required API and minimum service version in its release notes. Different version numbers alone are fine. Follow [Updates](#updates) if the service needs updating; changing the token does not fix this. |
| Controls are unavailable | Check the app's **Read-only** switch and USB/LAN connection. After changing app settings, save and restart the app, then wait for fresh status. |
| App exits immediately | Look for **Invalid or unreadable service configuration** in its log; the message names the invalid option or startup phase. |
| App runs but cannot reach the wallbox | Check adapter path, bus settings and exclusive bus ownership. A missing serial device is retried. |
| LAN adapter connects but readings fail | Check transparent RTU-over-TCP versus translating Modbus-TCP mode, device address and gateway serial settings; see [LAN troubleshooting](network-gateway.md#network-access-and-troubleshooting). |
| Error 200 or an occasional write timeout | Download integration diagnostics **before restarting the app**, then follow [heartbeat troubleshooting](troubleshooting.md#error-200-energy-manager-unavailable). |

The log shows service startup, USB/LAN connection and heartbeat start/stop.
Connection and heartbeat failures are logged when their state changes.
Watchdog can restart an exited app; recurring failures still need their cause fixed.

For rejected data, the HA log names the affected field without exposing the
access token. An unusable optional maximum-current reading does not block setup;
configured command limits still apply.

## More detail

- [Standalone Docker or Python deployment](service-standalone.md)
- [LAN-to-RS-485 adapter setup](network-gateway.md)
- [Service state, diagnostics, API and local development](service-reference.md)
- [Architecture](architecture.md) · [Security](../SECURITY.md) · [Privacy](../PRIVACY.md)
