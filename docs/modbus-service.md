# Modbus service: the preferred setup

The optional **Mennekes Amtron Modbus Service** keeps the wallbox connected while
Home Assistant Core restarts. It is the preferred setup when you want the
heartbeat and your chosen charging state to continue independently of HA.
Direct USB and LAN connections remain available without the extra service.

The service can reach the RTU wallbox through **USB or a LAN-to-RS-485 adapter**.
LAN needs no USB device on its host; see [LAN setup](network-gateway.md) for the
two supported protocols. Compatible Waveshare adapters also have optional
[configuration from the app](lan-adapters.md#service-app).

Its **Open Web UI** pages show an overview, set up the connection and run
[charging jobs and guest charging](app-guide.md) inside the app, so they continue
while HA is offline. Install the app and the integration in the same version.
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

Already using the service? Go to [Updates](#updates); keep your existing entry.

### 2. Install the app

[![Add the Mennekes Amtron app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps)

Or add this repository under **Settings → Apps → Install app → ⋮ → Repositories**:

```text
https://github.com/memphi2/ha-mennekes-amtron#apps
```

Install **Mennekes Amtron Modbus Service**. During installation, Supervisor may
show **Building**. Enable **Start on boot** and **Watchdog**.

The repository above provides stable releases. The separate
`https://github.com/memphi2/ha-mennekes-amtron#apps-beta` catalog carries
prereleases such as 0.9.0b2 and also receives stable releases. To switch
catalogs, follow the [channel handover](#updates).

### 3. Configure and start read-only

If another integration owns the USB or LAN connection, complete the
[handover preparation](#move-an-existing-wallbox) before starting the app.

Start the app and choose **Open Web UI → Connection** as an administrator;
this needs **Supervisor 2026.10.4+**. Choose USB or LAN; USB
provides an adapter dropdown, while LAN shows the host/IP and port.
A missing USB adapter is never replaced automatically. Saving LAN removes its
old serial path. Save and restart the app from the Info tab to apply changes.
Without a configured connection, the app remains running for setup and does not
start Modbus or discovery. Leave **Read-only** on in the native Configuration tab.

Without the web page, use the native **Configuration** tab; enable
**Show unused optional configuration options** if a required field is hidden.

| App option | What to enter |
|---|---|
| Wallbox connection | **USB / RS-485** (default), **LAN: Modbus RTU (TCP)** for a transparent adapter, or **LAN: Modbus TCP** for a translating gateway. |
| Serial adapter | USB only: select the adapter, preferably `/dev/serial/by-id/...`. Leave unset for LAN. |
| Adapter host / IP + TCP port | LAN: gateway hostname/IP and listening port, default **502**. No URL scheme or path. For USB, leave the host empty. |
| Wallbox address | The configured address; factory default **50**. |
| Baud rate / serial frame | USB only: match the wallbox; factory defaults **57600 / 8n2**. For LAN, configure these on the adapter or use [compatible adapter setup](lan-adapters.md#service-app). |
| Read-only | Leave **on** while checking the readings. |
| Current cap | The permitted charging current for your installation; default **6 A**. |
| Status / measurement interval | Keep **5 / 30 seconds** initially. The measurement interval helps limit database traffic. |

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
| Change Modbus polling intervals | **Open Web UI → Runtime**: preview, confirm, applies at the next poll without restart. |
| Change the service current cap or the waits between charging changes | **Open Web UI → Runtime** or the app configuration, then restart the app. **Minimum time between state changes** defaults to **300** seconds, range **0–3600**. |
| Change USB/LAN connection settings | **Open Web UI → Connection**, save and restart the app. For LAN, use the adapter interface or optional [adapter setup](lan-adapters.md#service-app). |
| Let the app charge on its own, or charge a guest's car | **Open Web UI → Charging jobs** or **Guest charging**; see [the app's web pages](app-guide.md). |
| See what the app is doing | **Open Web UI → Overview**. |
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

Early pause/resume, phase, release and lock changes
wait in one pending slot. **Pending change** and its `due` attribute show
the action and when it can run. A newer charging request replaces it. A stop
replaces a pending start and also waits for its due time. Heartbeat and recovery
that preserves the current intent keep running. Zero disables the longer wait,
while technical write guards remain.
See [Waiting between charging changes](user-guide.md#waiting-between-charging-changes).

For phase changes, the app confirms that the vehicle has paused, waits
the configured **Extra wait before phase changes** (default 5 seconds, range
0–30), then checks the state again before switching. Configure this separately
from the 300-second state-change interval in the app/service settings.

When fresh EVSE Idle and CP A1/A2 readings confirm no vehicle is connected,
the long interval and vehicle grace time are skipped. Pending waits check for
unplugging every five seconds. Unknown status retains the wait; five-second
register-write guards and heartbeat remain active.

### What continues during an outage?

| Event | What happens |
|---|---|
| HA Core restarts or stays offline | The running app keeps heartbeat and accepted charging intent, and a charging job keeps running. An already queued command can execute while HA is offline, up to its expiry if it belongs to a timed departure plan. |
| HA automation is unavailable | No new schedule or surplus calculation runs; the app holds the last chosen state. |
| App, host, power, USB or LAN/gateway connection fails | The heartbeat can stop. The wallbox's configured fallback applies. |
| HA reconnects | HA reads the current app state; it does not restore old commands. |

An app/service restart cancels pending commands and pauses a running charging
job, which continues only if the wallbox proves the car stayed plugged in; it
does not cancel an already applied pause. HA Core restarts leave the independent
service's queue running.

Configure and understand the wallbox's [fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).
The service does not remove the need for it. Include app data in backups:
it contains saved charging intent, a charging job and the private access token.

The app saves changed settings, not a growing history. Heartbeat history records
health changes rather than every beat. For smaller HA history, use a
[Recorder profile](recorder.md).

## AppArmor protection

The app includes a custom AppArmor profile.
Supervisor loads it automatically when the app is installed;
keep **Protection mode on**. It limits the service to its runtime files, USB/LAN
communication and its own token, charging-state and charging-job files. App options remain
read-only to the service. Normal heartbeat and charging operation are intended
to continue within these limits.

With the default app settings, an installed app can show **8/8** once Supervisor
has loaded its custom profile. A store rating of **7/8 before installation** is
normal. The [rating describes configuration](https://contributors.home-assistant.io/apps/reference/security-rating/),
not a completed hardware test.

If the app cannot start or an operation is denied, check Supervisor logs and
host kernel entries containing `apparmor="DENIED"`. Report the rejected operation
and path, removing private identifiers. Keep protection enabled while the cause
is investigated; changing the token does not resolve a policy denial.

This service profile is not applied automatically to standalone Docker or direct
HA connections. Standalone deployments need explicit host/profile configuration;
direct mode uses Home Assistant's own host/container security policy.

## Updates

Update the app and the integration together. **Update the app first**, wait
until it has started, then update the integration and restart Core. Use **Check
for updates** in the app store; for a separate service, follow the
[standalone update steps](service-standalone.md#operation-and-updates).
**Updating the app briefly interrupts its heartbeat** and pauses a running
charging job until the wallbox proves the same plug-in; see
[after an app restart](app-guide.md#after-an-app-restart-or-a-connection-loss).
An existing app updates in place with its data intact.

Stable releases use `#apps`; prereleases use `#apps-beta`, which also
receives stable releases. Switching between stable and beta catalogs creates a different app in Supervisor.
For that channel change, back up app data, note the charging settings and
disconnect the vehicle. Copy the options, set the new app to **Read-only**, stop
the old app before starting the new one, and confirm its discovery.
The new app has separate saved state: record and verify pause, current and
charging-release settings as in the [first handover](#move-an-existing-wallbox).
Keep the old app and backup until the handover is verified.

## If something is wrong

For optional vehicle targets, profiles or departure planning, use the
[feature troubleshooting guide](troubleshooting.md#targets-and-departure-planning).

| Symptom | First check |
|---|---|
| HA asks for a URL or token | Confirm that the app is running, then use its discovery card or **Connect to the Modbus service**. Manual credentials are for standalone services. |
| App not ready | Authentication succeeded, but wallbox data is unavailable. Check the app log for USB/LAN connection and initialization errors, then retry. No token change is needed. |
| Token rejected | During setup, retry discovery confirmation. For an existing connection, HA tries to refresh access automatically; if an authentication notice remains, [renew service access](troubleshooting.md#renew-service-access). No manual app token is needed. |
| Service API or minimum version is incompatible | Update the app and the integration to the same release, app first; see [Updates](#updates). Changing the token does not fix this. |
| HA reports that a charging job of the app controls charging | An app charging job runs for the plugged-in car. Stop it on **Charging jobs**, or unplug the car; see [charging jobs](troubleshooting.md#charging-jobs-in-the-app). |
| Controls are unavailable | Check the app's **Read-only** switch and USB/LAN connection. After changing app settings, save and restart the app, then wait for fresh status. |
| App exits immediately | Look for **Invalid or unreadable service configuration** in its log; the message names the invalid option or startup phase. |
| App runs but cannot reach the wallbox | Check adapter path, bus settings and exclusive bus ownership. A missing serial device is retried. |
| LAN adapter connects but readings fail | Check transparent RTU-over-TCP versus translating Modbus-TCP mode, device address and gateway serial settings; see [LAN troubleshooting](network-gateway.md#network-access-and-troubleshooting). |
| Error 200 or an occasional write timeout | Download integration diagnostics **before restarting the app**, then follow [heartbeat troubleshooting](troubleshooting.md#error-200-energy-manager-unavailable). |

The log shows service startup, USB/LAN connection and heartbeat start/stop.
The [live communication view](troubleshooting.md#watch-app-modbus-communication-live)
under **Open Web UI → Debug log** (**Debugprotokoll** in German) is optional and
off by default; the **Registers** page shows the last value of every register.
Connection and heartbeat failures are logged when their state changes.
Home Assistant also shows a repair after a continuous 60-second heartbeat or
service-connection problem in a managed, write-enabled setup. It clears
automatically on recovery. An unreachable service does not prove its heartbeat
has stopped; see [connection repairs](troubleshooting.md#heartbeat-or-service-connection-repair).
Watchdog can restart an exited app; recurring failures still need their cause fixed.

For rejected data, the HA log names the affected field without exposing the
access token. An unusable optional maximum-current reading does not block setup;
configured command limits still apply.

## More detail

- [Standalone Docker or Python deployment](service-standalone.md)
- [LAN-to-RS-485 adapter setup](network-gateway.md)
- [Service state, diagnostics, API and local development](service-reference.md)
- [Architecture](architecture.md) · [Security](../SECURITY.md) · [Privacy](../PRIVACY.md)
