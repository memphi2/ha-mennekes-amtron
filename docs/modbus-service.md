# Modbus service: the preferred setup

The optional **Mennekes Amtron Modbus Service** keeps the wallbox connected while
Home Assistant Core restarts. It is the preferred setup when you want the
heartbeat and your chosen charging state to continue independently of HA.
Direct serial operation remains available without the extra service.

**Current beta: 0.5.0b5.** The integration and app are installed separately.
Stable users can stay on 0.4.1. This beta still needs hardware testing; see
[what to check](#check-the-handover).

[Install on HA OS](#install-the-beta-on-home-assistant-os) ·
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
   while the serial adapter changes owners, so the wallbox may apply its fallback.
3. **Disable the existing direct-serial integration entry. Do not delete it.**
   This releases the serial adapter for the app.

Continue below. For a new installation, start directly with the installation
steps. In either case, the app must be the only Modbus master on the bus;
read-only mode also sends requests. Check the [wiring and bus settings](hardware.md)
if the wallbox has not yet been prepared.

## Install the beta on Home Assistant OS

### 1. Install the integration

Install **Mennekes Amtron RTU 0.5.0b5** and restart Home Assistant Core.
On HA 2026.11+, enable **Pre-release** for the repository in the Marketplace.
On HA 2026.9/2026.10, enable beta releases in HACS.

Already using beta 4? Go to [Updates](#updates); you do not need a new setup.

### 2. Install the app

[![Add the Mennekes Amtron beta app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps-beta)

Or add this repository under **Settings → Apps → Install app → ⋮ → Repositories**:

```text
https://github.com/memphi2/ha-mennekes-amtron#apps-beta
```

Install **Mennekes Amtron Modbus Service**. During installation, Supervisor may
show **Building**. Enable **Start on boot** and **Watchdog**.

### 3. Configure and start read-only

If another integration owns the serial adapter, complete the
[handover preparation](#move-an-existing-wallbox) before starting the app.

| App option | What to enter |
|---|---|
| Serial adapter | Your USB RS-485 adapter; prefer a stable `/dev/serial/by-id/...` path. |
| Wallbox address | The configured address; factory default **50**. |
| Baud rate / serial frame | Match the wallbox; factory defaults **57600 / 8n2**. |
| Read-only | Leave **on** while checking the readings. |
| Current cap | The permitted charging current for your installation; default **6 A**. |
| Status / measurement interval | Keep **5 / 30 seconds** initially. The measurement interval helps limit database traffic. |

Leave network settings at their defaults. No URL, token or published network
port is needed for the HA OS app. Start it and check its **Log** for the serial
connection. Read-only mode deliberately leaves the heartbeat off.

### 4. Connect Home Assistant

Open **Settings → Devices & services** and configure the discovered
**Mennekes Amtron** entry. The app supplies its address and token automatically.
An existing entry for the same wallbox is reused; re-enable it if it remains
disabled after the handover.

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
| Change Modbus polling intervals or the service current cap | Open the app configuration. |
| Change how often HA reads app status, or lower HA's current cap | Open the integration options. |
| Hand the serial adapter to another master | Stop the app. Disabling its HA entry or enabling read-only does not release the bus. |

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
| App, host, power or USB connection fails | The heartbeat can stop. The wallbox's configured fallback applies. |
| HA reconnects | HA reads the current app state; it does not restore old commands. |

Configure and understand the wallbox's [fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).
The service does not remove the need for it. Include app data in backups:
it contains saved charging intent and the private access token.

The app saves changed settings, not a growing history. Heartbeat history records
health changes rather than every beat. For smaller HA history, use a
[Recorder profile](recorder.md).

## Updates

Update the integration first and restart Home Assistant Core. Update the app
separately through Supervisor when offered. A Core restart leaves the running
app's heartbeat active; **an app update briefly interrupts it**.

**From beta 4 to beta 5:** keep the existing entry. The running **0.5.0b4** app
already supplies the status needed for HA controls to follow its **Read-only**
switch. The fix works as soon as the **0.5.0b5 integration** is loaded. Use
**Check for updates** in the app store to obtain the matching **0.5.0b5 app**.
There is no need to delete the integration or enter a URL/token again.

The beta and stable repositories end in `#apps-beta` and `#apps`. Supervisor
regards them as different apps. To change channels, back up app data, copy the
options, stop the old app before starting the new one, and confirm its discovery.
Keep the old app and backup until the handover is verified.

## If something is wrong

| Symptom | First check |
|---|---|
| HA asks for a URL or token | Confirm that the app is running, then use its discovery card or **Connect to the Modbus service**. Manual credentials are for standalone services. |
| App not ready / token rejected | Check the app log and retry discovery confirmation. HA retrieves the current credentials; no app restart is needed just to refresh them. |
| Controls are unavailable | Check the app's **Read-only** switch and serial connection. After changing app settings, save and restart the app, then wait for fresh status. |
| App exits immediately | Look for **Invalid or unreadable service configuration** in its log; the message names the invalid option or startup phase. |
| App runs but cannot reach the wallbox | Check adapter path, bus settings and exclusive bus ownership. A missing serial device is retried. |
| Error 200 or an occasional write timeout | Download integration diagnostics **before restarting the app**, then follow [heartbeat troubleshooting](troubleshooting.md#error-200-energy-manager-unavailable). |

The log shows service startup, serial connection and heartbeat start/stop.
Connection and heartbeat failures are logged when their state changes.
Watchdog can restart an exited app; recurring failures still need their cause fixed.

For rejected data, the HA log names the affected field without exposing the
access token. An unusable optional maximum-current reading does not block setup;
configured command limits still apply.

## More detail

- [Standalone Docker or Python deployment](service-standalone.md)
- [Service state, diagnostics, API and local development](service-reference.md)
- [Architecture](architecture.md) · [Security](../SECURITY.md) · [Privacy](../PRIVACY.md)
