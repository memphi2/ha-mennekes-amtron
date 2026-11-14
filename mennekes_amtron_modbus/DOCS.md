# Set up the Mennekes Amtron app

This optional app keeps the heartbeat and last charging state running through
Home Assistant Core restarts. It owns the USB or LAN adapter; install the
**Mennekes Amtron RTU integration** separately for the HA controls.

**Start here:** [First setup](#first-setup) · [Daily use](#daily-use) ·
[Updates](#updates) · [Help](#help)

## First setup

Install the **Mennekes Amtron RTU** integration and check its minimum app version
and complete any requested Home Assistant Core restart. See
[Installation](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/installation.md).

1. **Prepare an existing wallbox.** Note its pause, current limit, resume current
   and charging-release settings. Disconnect the vehicle for the first handover.
   Disable an existing direct USB or LAN integration entry to release its
   connection; keep the entry so its device and entity IDs are retained.
2. **Configure the connection.** Leave **Read-only on**, start the app and select
   **Open Web UI → Connection**. Choose **USB / RS-485** or the matching LAN
   protocol. USB shows a dropdown of detected adapters; LAN shows the host/IP
   and TCP port. Enter the wallbox address and save. For LAN, set baud rate and
   frame on the adapter, or use the optional compatible-adapter setup on the same
   page. Without a saved connection, the app only waits for setup; it opens no
   Modbus connection and publishes no discovery.
3. **Restart the app.** Restart it from its **Info** tab to apply the saved
   connection. Enable **Start on boot** and **Watchdog**. Check the log for the
   USB/LAN connection. Read-only mode deliberately leaves the heartbeat off.
4. **Connect HA.** Under **Settings → Devices & services**, configure the
   discovered **Mennekes Amtron** entry. Address and token are automatic; there is
   nothing to copy. An existing entry for the same wallbox is reused; re-enable
   it if it stays disabled. Keep the app **Read-only** while you compare the
   readings with the wallbox.
5. **Enable control.** Set the permitted current cap, turn **Read-only off** in
   the app's **Configuration** tab, save and restart the app. HA enables its
   controls after fresh writable status; there is no second HA write switch and
   no HA restart is needed. The app restart briefly interrupts the heartbeat.
6. **Check the handover.** Reapply and verify the noted pause, current and
   release settings before reconnecting the vehicle. Test a deliberate pause and
   an HA Core restart: the app should stay running and the pause should remain.

## Connection settings

| Connection | Enter |
|---|---|
| USB / RS-485 | Select the serial adapter. Factory wallbox settings: address **50**, **57600 baud**, **8n2**. |
| LAN: Modbus RTU (TCP) | Transparent TCP-server adapter: its host/IP, TCP port and wallbox address. |
| LAN: Modbus TCP | Translating gateway: its host/IP, TCP port and wallbox address. |

LAN serial parameters belong on the adapter. The TCP port defaults to **502**;
use the port actually configured on the adapter. Leave the app's API settings
at their defaults: its HTTP port **8765** is unrelated to the adapter port.
HA OS discovery supplies the service URL and token automatically.

[LAN setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/network-gateway.md) · [Optional Waveshare setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/lan-adapters.md#service-app)

## Daily use

| Task | Where |
|---|---|
| Current and pause | HA wallbox controls; **0 A removes the limit**, it does not pause. |
| Add dashboard cards | [Templates in five languages](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/app-from-home-assistant.md#dashboard-with-mushroom-cards) |
| Targets, next departure or guest charging | **Open Web UI → Charging**; saved choices are shared with HA |
| Current cap, polling and waits | **Settings → Runtime**: preview and confirm; no restart |
| USB or LAN settings | **Settings → Connection**: save and restart app |
| Read-only | Native **Configuration**: save and restart app; this also controls HA write access |
| Live decoded Modbus traffic | **Diagnostics → Log** |

Web pages need **Supervisor 2026.10.4+** and an administrator account. The native
Configuration tab remains available. Default polling is **5 / 30 seconds**;
the state-change wait is **300 seconds**. A queued request is not a completed
command: check **Pending change**. The app is the only software current cap in
service mode. Its cap does not apply to a deliberately selected unlimited **0 A**.

An explicitly started guest charge runs independently of HA. It grants permission
for the current connection and lifts manual pause, but never unlocks the wallbox. Use the ordinary pause switch to stop. After an app restart, guest permission and limits are restored only for the
proven same plug-in; otherwise charging stops and needs a new guest start.
There is no separate saved guest order.
[App operation and restart behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/app-guide.md).

The app also continues an accepted departure plan during HA downtime. HA
supplies new vehicle reports and runs calendar/climate automations. An app,
host or adapter failure can still interrupt the heartbeat: prepare the
[fallback](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/safety.md#the-heartbeat-and-what-happens-without-it).
Stop the app before giving the bus to another owner; read-only still polls.

## Updates

App and integration versions need not match. Follow the integration's minimum
app/API requirement. When updating both, **update the app first**, wait for it
to start, then update the integration. Keep the existing app data and HA entry.
App updates interrupt the heartbeat briefly. Back up app data, including its
private token and saved charging state.

Before 1.0, incompatible beta formats are not migrated. A rejected saved state
raises an HA Repair. Acknowledging it keeps pause on and charging release off;
check targets and permissions before resuming.
[Update and reset instructions](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/installation.md#updates).

Stable apps use **#apps**; betas use **#apps-beta**. Changing catalogs creates a
separate app with separate data. Follow the
[channel handover](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/modbus-service.md#updates), keeping only one bus owner.

## AppArmor protection

Keep **Protection mode on**. Supervisor loads the app's supplied AppArmor
profile. If an operation is denied, collect the Supervisor log and any
`apparmor="DENIED"` message with private details removed.
[Protection and security-rating explanation](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/modbus-service.md#apparmor-protection).

## Help

| Symptom | First check |
|---|---|
| Connection setup required | Choose USB or LAN on Connection, save and restart. |
| No discovery | App running and wallbox initialized? Use HA **Add integration → Connect to the Modbus service**. |
| Controls unavailable | Read-only off, app restarted, fresh wallbox readings? |
| Not ready | Check USB/LAN connection in the app log; changing the token does not fix missing wallbox data. |
| Invalid configuration | The startup log names the invalid option or phase. |
| Token or version rejected | [Connection help](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/troubleshooting.md#app-connection-and-discovery) |
| Heartbeat fault | Download HA diagnostics before restarting; [heartbeat help](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/troubleshooting.md#heartbeat-or-service-connection-repair). |

**Diagnostics → Log** shows decoded Modbus traffic without adding bus requests
or Recorder rows. It is bounded in RAM and redacts identifying register values;
[details](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/troubleshooting.md#watch-app-modbus-communication-live).

[User guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/README.md) · [App pages](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/app-guide.md) · [Troubleshooting](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b8/docs/troubleshooting.md)
