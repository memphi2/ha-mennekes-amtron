# Mennekes Amtron Modbus Service

Keep the wallbox heartbeat running through Home Assistant Core restarts.
This optional app is the preferred way to operate the integration: it holds
the last charging state, including a deliberate pause, while HA is offline.

**Beta 0.5.2b1 · amd64 and aarch64 · separate integration required**

This prerelease is available through `#apps-beta`. The stable `#apps` catalog
stays on **0.5.1**. See [Beta testing](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b1/docs/installation.md#beta-testing-052b1)
to select the matching integration prerelease and prepare an app update.

LAN-to-RS-485 adapters are supported without local USB.
See the [LAN guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b1/docs/network-gateway.md)
for transparent RTU over TCP versus translating Modbus TCP-to-RTU gateways.

[First setup](#first-setup) · [Daily use](#daily-use) ·
[Updates](#updates) · [Help](#help)

## First setup

For a new setup, install the **Mennekes Amtron RTU integration** and restart Home
Assistant Core. For beta testing, select **0.5.2b1** in the Marketplace or HACS
using the [beta guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b1/docs/installation.md#beta-testing-052b1).
The integration and this app are
separate installations. Already using the app? Follow [Updates](#updates)
instead: the app and integration can be updated independently.

1. **Prepare an existing wallbox.** Note its pause, current limit, resume current
   and charging-release settings. Disconnect the vehicle for the first handover.
   Disable the existing direct USB or LAN integration entry to release its connection;
   keep the entry so its device and entity IDs are retained.
2. **Configure the app.** Choose **USB / RS-485** or the matching LAN protocol.
   Enter the wallbox address and connection settings below. For LAN, configure
   baud rate/frame on the gateway. Leave **Read-only on** initially.
3. **Start the app.** Enable **Start on boot** and **Watchdog**. Check the log for
   its USB/LAN connection. Read-only mode deliberately leaves the heartbeat off.
4. **Connect HA.** Under **Settings → Devices & services**, configure the
   discovered **Mennekes Amtron** entry. Address and token are automatic; there is
   nothing to copy. The existing entry is reused for the same wallbox. Re-enable
   it if it remains disabled. Keep the app **Read-only** while doing this and
   comparing readings with the wallbox.
5. **Enable control.** Set the permitted current cap, turn **Read-only off in the
   app**, save and restart the app. Its settings are read at startup. HA enables
   its controls after fresh writable status; there is no second HA write switch
   and no HA restart is needed. The app restart briefly interrupts its heartbeat.
6. **Check the handover.** Reapply and verify the noted pause/current/release
   settings before reconnecting the vehicle. Test a deliberate pause and an HA
   Core restart: the app should stay running and the pause should remain active.

| App option | Initial setting |
|---|---|
| Wallbox connection | **USB / RS-485** (default), **LAN: Modbus RTU (TCP)** (transparent adapter), or **LAN: Modbus TCP** (translating gateway). |
| Serial adapter | USB only: select the adapter; prefer `/dev/serial/by-id/...`. Leave unset for LAN. |
| Adapter host / IP + TCP port | LAN: hostname/IP without a URL scheme, and gateway port; default **502**. For USB, leave the host empty. |
| Wallbox address | Match the wallbox; factory default **50**. |
| Baud rate / frame | USB only: match the wallbox, factory defaults **57600 / 8n2**. For LAN, configure these on the gateway. |
| Read-only | **On** until readings are checked. |
| Current cap | Default **6 A**; set within your installation's permitted current. |
| Status / measurement interval | Keep **5 / 30 seconds** initially. |

**Next beta, unreleased:** **Minimum time between state changes** defaults to
**300 seconds**, with a range of **0–3600 seconds**. This option is not in
published 0.5.2b1. Zero disables the longer wait; the technical write guards
remain. Save changes and restart this app. HA does not apply a second interval.

**Adapter host / IP** is always visible; enter it for LAN and
leave it empty for USB. If **Serial adapter** is hidden, enable **Show unused
optional configuration options** on the Configuration tab. The form does not
show or hide other fields when you change the connection choice.

App **0.5.2b1** shows exactly three connection choices. If an older app still has
a technical name saved, select its matching readable choice once:
`serial` → **USB / RS-485**, `rtu_over_tcp` → **LAN: Modbus RTU (TCP)**,
`modbus_tcp` → **LAN: Modbus TCP**. App 0.5.1 already lets you save this choice
before updating. Keep your existing adapter/IP and other settings. If an update
rejects an old choice, select and save the matching choice in Configuration,
then start the app. No invalid choice falls back to USB. For a missing host field
in 0.5.0, use the [older-app workaround](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.1/docs/network-gateway.md#app-050-configuration).

Leave the app's API settings at their defaults: HTTP port **8765** is separate
from a LAN adapter's Modbus port. No host port needs publishing and no service
token needs configuring. Use a trusted private LAN for gateways. Only this app
may control the bus through its USB or LAN adapter, even when read-only.

On first installation, the app reads the wallbox's current state. If fallback
changed that state during handover, it cannot infer your earlier intention.
A paused wallbox without saved app state uses **6 A** as its first resume target.

## Daily use

- **Pause/resume:** use **Charging paused** in HA.
- **Current limit:** use the HA slider. **0 A removes the EMS limit; it does not
  pause charging.** The app and HA caps constrain limited-current commands;
  the wallbox's hardware limits apply even at 0 A.
- **Write permission:** change the app's **Read-only** switch, save and restart
  the app. No HA restart is needed. HA controls remain registered but unavailable
  while read-only or while the app is not ready. HA does not send old commands
  when permission changes. The service may still recover error 200 automatically
  by reasserting its saved state, including a deliberate pause.
- **Heartbeat status:** check HA's **Heartbeat** diagnostic sensor. It records
  health changes rather than every beat.

In the next beta, an early start or phase change is queued. HA's **Pending
change** and **Pending change due** show the request and its scheduled time.
The latest charging request replaces the old one, and stopping cancels a
pending start. A pause within the five-second current-write interval cancels
pending work but returns a short wait error; request pause again after that
wait. A fixed due timestamp supplies the remaining-time display without
database updates every second. Pause/release/lock/phase readback still shows the
actual wallbox state; the slider while paused shows the remembered resume
target, which may already reflect a queued request. Heartbeat and recovery that preserves the existing intent keep
running during a wait.

HA can remain offline indefinitely while the app holds the last charging state.
Automations cannot produce new settings during that outage. In the next beta,
the running app can still execute a command that was queued before HA stopped.
Restarting the app itself cancels pending commands, while retaining accepted
charging intent. **App, host, power
or USB/LAN/gateway failures still interrupt the heartbeat** and use the wallbox's configured
fallback. Switching the app to read-only also stops the heartbeat.

To give the serial bus to another master, stop the app. Disabling the HA entry
or selecting read-only does not release it. Include app data in backups; it
contains the saved charging intent and private access token. Saved settings
replace a small file rather than accumulating a history.

## Updates

**The app does not need an update whenever the integration changes.** Their
version numbers may differ. The current integration needs **service 0.5.0 or
newer with API 2**; this app already meets that requirement. Updating only the
integration and restarting Core leaves the app and its heartbeat running with
writes enabled.

Update the app when its own update is offered or the integration's release notes
require a newer service. Use **Check for updates** in the app store. **If both
components need updating, update the app first**, wait for initialization, then
update the integration and restart Core. **Updating the app itself briefly
interrupts the heartbeat.** Keep its data and the existing HA entry.

**From a 0.5.0 beta:** update this app to **0.5.2b1 first**, then the
integration. The old beta integration may show unavailable controls between
updates because its API differs. Keep the existing beta app and its data; no
reinstall is needed. If an older technical connection choice is rejected,
select its matching readable name as described above before starting the app.
The token, saved charging state and USB settings are retained; switching to LAN
is optional.

The beta repository ends in `#apps-beta` and offers **0.5.2b1**;
the stable-only repository uses `#apps` and stays on **0.5.1**. Switching catalogs is optional and
creates a different app in Supervisor. For that change, back up the data, note
the charging settings and disconnect the vehicle. Copy the options, set the new
app to **Read-only**, stop the old app before starting the new one, and confirm
its discovery. Keep the old app and backup until the handover is verified.
The new app has separate saved state: note and verify pause, current and release
settings as in the first setup before reconnecting the vehicle.

## AppArmor protection

**Included in 0.5.2b1:** stable 0.5.1 does not contain this custom profile.
Supervisor loads it automatically when installing this beta app.
Keep **Protection mode on**. The profile allows USB/LAN communication, heartbeat
and the service's own token and saved charging state; app options remain
read-only to the service.

With default settings, **8/8** is possible after the installed custom profile
has loaded. **7/8 before installation** is normal; the store rating is not
proof of hardware testing. See [Supervisor's rating rules](https://contributors.home-assistant.io/apps/reference/security-rating/).

If AppArmor denies an operation, inspect Supervisor logs and the host kernel
log for `apparmor="DENIED"`. Report the operation and path with private details
removed. Keep protection enabled while investigating the rejected access.
Standalone Docker needs explicit host/profile configuration; direct HA mode
uses HA's own security policy and does not automatically use this service profile.

## Help

| Symptom | What to check |
|---|---|
| No discovered entry | Confirm the app is running. In HA, choose **Add integration → Mennekes Amtron RTU → Connect to the Modbus service** to look it up again. |
| Not ready | Authentication succeeded, but wallbox data is unavailable. Check the app log for USB/LAN connection and initialization errors, then retry. No token change is needed. |
| Token rejected | During setup, retry discovery confirmation. For an existing connection, HA first tries to refresh access automatically. If an authentication prompt remains in Devices & services, confirm it; no token entry is needed. See [renew service access](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b1/docs/troubleshooting.md#renew-service-access). |
| Service API or minimum version is incompatible | Check the API and minimum service version required in the integration’s release notes. Version numbers do not need to match. Follow [Updates](#updates) if needed; changing the token does not fix this. |
| Controls unavailable | Check **Read-only**. After changing it, save and restart the app, then wait for fresh status. Check the USB/LAN connection if controls remain unavailable. |
| App stops immediately | The log's **Invalid or unreadable service configuration** message names the invalid setting or startup phase. |
| Heartbeat or write fault | Download integration diagnostics before restarting the app, then check the log. |

The log shows service startup, the USB/LAN connection and heartbeat start/stop.
Diagnostics include the last 20 failed write/recovery records in RAM, with timing
information and no packet contents or credentials. They add no Recorder rows and
are lost when the app restarts.

Verify deliberate pause, HA restart, app restart and reconnection on your own
hardware. Error-200 recovery while paused and particular LAN gateways still need
confirmation on the actual hardware; automated tests do not establish that.

[Full setup, fallback and troubleshooting guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b1/docs/modbus-service.md)
