# Mennekes Amtron Modbus Service

Keep the wallbox heartbeat running through Home Assistant Core restarts.
This optional app is the preferred way to operate the integration: it holds
the last charging state, including a deliberate pause, while HA is offline.

**amd64 and aarch64 · separate integration required**

Beta **0.8.0b6** is available through **#apps-beta**; **#apps** keeps stable
**0.6.1**. Existing beta-catalog installations update in place. Keep the app data
and HA entry. For all beta features and fixes, update this app before integration
**0.8.0b6**. Switching catalogs creates a separate app and requires a handover.
See [Updates](#updates).

**New in 0.8.0b7:** a single lost Modbus reply that a resend made good is
counted as a retry instead of being logged as an error, and no longer marks the
connection quality as degraded. Only transactions that finally fail count as
failures.

**Since 0.8.0b6:** a percentage target at the car's own charge limit no
longer pauses the wallbox when reached. The car ends that charge itself, and
the session stays as it is until you unplug. A lower target still pauses.
Virtual Plug & Charge keeps its grant through a briefly unreadable or
contradictory wallbox status instead of switching the release off until the
next plug-in.

**Since 0.8.0b4:** a single disturbed bus read no longer makes the affected
values briefly unavailable in Home Assistant; recently read status and
measurements are kept for up to 30 seconds. The energy meter and error recovery
still use only fresh reads.

**Since 0.8.0b3:** while the charging release is off or no car is connected, a
current change such as ending a pause no longer starts or waits for the
state-change interval. Virtual Plug & Charge can therefore end a manual pause and
release a recognized car immediately. Since 0.8.0b2, decoded Modbus communication
has its own **Debug log** tab beside **Connection**.
The app retains optional Virtual Plug & Charge, Supervisor **2026.10.4+** guided
setup, target-saving fixes and visible TCP-login/password fields from 0.8.0b1.
Existing connections keep their settings until you change them.

LAN-to-RS-485 adapters are supported without local USB.
See the [LAN guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/network-gateway.md)
for transparent RTU over TCP versus translating Modbus TCP. Supported Waveshare
models also offer optional [LAN adapter setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/lan-adapters.md#service-app).

[First setup](#first-setup) · [Daily use](#daily-use) ·
[Updates](#updates) · [Help](#help)

## First setup

For a new beta setup, enable beta versions in HACS and select **Mennekes Amtron
RTU integration 0.8.0b6**. Complete any requested Home Assistant Core restart.
See [Installation](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/installation.md).
The integration and this app are
separate installations. Already using the app? Follow [Updates](#updates)
instead: the app and integration can be updated independently.

1. **Prepare an existing wallbox.** Note its pause, current limit, resume current
   and charging-release settings. Disconnect the vehicle for the first handover.
   Disable the existing direct USB or LAN integration entry to release its connection;
   keep the entry so its device and entity IDs are retained.
2. **Start and configure the app.** Leave **Read-only on**, start the app and
   choose **Open Web UI**. Select **USB / RS-485** or the matching LAN protocol.
   USB shows a dropdown of detected adapters; LAN shows the host/IP and TCP port.
   Enter the wallbox address and save. For LAN, set baud rate/frame on the adapter
   or use the optional compatible-adapter setup in the connection page.
   With no connection configured, the app stays running for setup without opening
   a Modbus connection or publishing discovery.
3. **Restart the app.** Return to its **Info** tab and restart to apply the saved
   connection. Enable **Start on boot** and **Watchdog**. Check the log for the
   USB/LAN connection. Read-only mode deliberately leaves the heartbeat off.
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
| Serial adapter | USB only: choose the adapter in **Open Web UI**; stable `/dev/serial/by-id/...` names are preferred automatically. Switching to LAN removes the old USB selection when saved. |
| Adapter host / IP + TCP port | LAN: hostname/IP without a URL scheme, and gateway port; default **502**. For USB, leave the host empty. |
| Wallbox address | Match the wallbox; factory default **50**. |
| Baud rate / frame | USB only: match the wallbox, factory defaults **57600 / 8n2**. For LAN, configure these on the adapter or use compatible-adapter setup. |
| Read-only | **On** until readings are checked. |
| Current cap | Default **6 A**; set within your installation's permitted current. |
| Status / measurement interval | Keep **5 / 30 seconds** initially. |

**Minimum time between state changes** defaults to
**300 seconds**, with a range of **0–3600 seconds**. Zero disables the longer wait; the technical write guards
remain. Save changes and restart this app. HA does not apply a second interval.

**Extra wait before phase changes** defaults to **5 seconds** and is
configurable from **0 to 30 seconds**. After the vehicle confirms a pause, the
app waits this extra time and checks the state again before changing phases.
Zero skips only the extra delay; pause confirmation and the state-change
interval still apply.

With no vehicle connected, fresh EVSE Idle and CP A1/A2 readings skip the long
state-change wait and vehicle grace time. Pending waits check for unplugging
every five seconds. Unknown or inconsistent status retains the wait;
five-second register-write guards and heartbeat remain active.

### Connection page

**Open Web UI** provides exactly three connection choices and shows only the
fields needed for that connection. A disconnected USB adapter is marked missing;
another adapter is never selected automatically. Reconnect it or explicitly
choose its replacement. Switching to LAN does not require the old USB adapter
to exist. Ordinary connection changes take effect when you restart the app;
that restart briefly interrupts the heartbeat.

Optional **LAN adapter setup** detects only the host you enter and lets you
review compatible adapter settings before applying them. This also selects
TCP-server mode and disables storage polling, autonomous preambles and old
serial-buffer replay. Unknown custom payloads are refused. Applying these settings
restarts the adapter and stops the app's old Modbus connection immediately.
After successful saving, restart the app to connect using the new settings.
Detection alone changes nothing. See [supported models, TCP login and recovery](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/lan-adapters.md).

The native **Configuration** tab contains manual connection fields; it does
not have an adapter detection button. To detect and configure a compatible
adapter, go to the app's **Info** tab, select **Open Web UI**, choose the LAN
protocol and enter the adapter's host. Select **Read LAN adapter settings**,
then select **Apply adapter settings when saving** and save. Reading inspects
only the entered host. Applying changes the adapter immediately; restart the
app afterwards to resume the connection. The native TCP-login switch changes
the app connection only; it does not enable login on the adapter.

The page requires a Home Assistant administrator and **Supervisor 2026.10.4+**.
You can also save LAN settings in the native **Configuration** tab, including
when an old USB adapter is absent. Use **Open Web UI** for the USB dropdown.
Read-only, current limits and polling intervals remain in the native Configuration
tab. Avoid editing both forms at once; reload the connection page after changing
native options.

The native Configuration form cannot show fields conditionally or require a
dropdown for its device selector; see the [Supervisor option schema](https://contributors.home-assistant.io/apps/guides/options/).
The separate [Ingress page](https://contributors.home-assistant.io/apps/guides/ingress/)
does not require a host port, an API token in your browser or extra Supervisor
roles. Supervisor 2026.10.4 supplies the required server-side administrator check.

App **0.6.0b1** shows exactly three connection choices. If an older app still has
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
- **Charging targets:** set an **Energy target** in kWh, or configure an optional
  vehicle percentage target through the integration. Targets only limit charging.
  See [target behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/user-guide.md#energy-target).
- **Next departure:** select a one-trip charging deadline in HA. This optional
  plan may resume a pause to meet the time; ordinary kWh/percentage limits alone
  never do. The service discards queued timed-plan commands after their deadline.
  Open the car's profile under **Configure → Vehicles**, set up its charge
  level, then enable **Next departure → Configure departure planning** and
  choose its battery-capacity source.
  See [departure planning](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/departure-planning.md).
- **Loss and cost estimates:** choose optional vehicle and price sources in the
  integration options. See [charging estimates](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/charging-estimates.md).

Early pause/resume, phase, release and lock changes are queued.
HA's **Pending change** and **Pending change due** show the request and its
scheduled time. The latest charging request replaces the old one. **A stop
request also waits**, replacing any pending start. A pause within the five-second
current-write interval is queued too and does not need to be requested again.
A fixed due timestamp supplies the remaining-time display during timed waits,
without database updates every second. While a phase change waits for vehicle
pause confirmation, its action stays visible but the due time is unknown. Once
confirmed, the timestamp shows the end of the extra wait.
Pause/release/lock/phase readback still shows the
actual wallbox state; the slider while paused shows the remembered resume
target, which may already reflect a queued request. Heartbeat and recovery that
preserves the existing intent keep running during a wait.

Before changing phases, the app waits up to 30 seconds for live confirmation
that the vehicle has paused, followed by the configured grace time. If the
confirmation or the final state check fails, the phase command is not sent and
the requested pause remains in place. Resume separately after a successful
phase change.
An idle wallbox without a connected vehicle does not need pause confirmation or
the extra wait. Fresh EVSE Idle and CP A1/A2 also skip the longer state-change
interval; the five-second register-write guards remain.

HA can remain offline indefinitely while the app holds the last charging state.
Automations cannot produce new settings during that outage. The running app can
still execute a command queued before HA stopped, but timed-plan commands expire
at their departure deadline. Expiry cancels only unsent work; it does not undo a
current or pause already applied.
Restarting the app itself cancels pending commands, while retaining accepted
charging intent. **App, host, power
or USB/LAN/gateway failures still interrupt the heartbeat** and use the wallbox's configured
fallback. Switching the app to read-only also stops the heartbeat.

To give the serial bus to another master, stop the app. Disabling the HA entry
or selecting read-only does not release it. Include app data in backups; it
contains the saved charging intent and private access token. Saved settings
replace a small file rather than accumulating a history.

### Virtual Plug & Charge

This feature is **optional and off by default**. Configure vehicle profiles and
fresh cable/home detection in each car's profile in HA, then switch on
**Virtual Plug & Charge** under **Configuration** on the wallbox device page.
From integration **0.8.0b6**, that switch is the only permission: every profile
with detection signals takes part. Automatic selection is chosen in the
**Active vehicle** entity; selecting a profile manually does not replace the
detection check. See the
[vehicle setup guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/vehicle-setup.md#optional-virtual-plug--charge).

The app initially withholds charging release until HA recognizes the selected
vehicle unambiguously. A grant is valid only for the current physical
connection. The app keeps it through HA Core restarts, even if cloud vehicle
signals later become unavailable. Unplugging or restarting the app clears it;
a new grant needs fresh recognition from HA. From **0.8.0b6**, a briefly
unreadable or contradictory wallbox state keeps the grant. After 60 seconds of
unknown state the release is switched off, and HA grants the same connection
again once the car is reported connected, without replugging. The log names
the reason whenever a grant ends, and warns when the wallbox status cannot be
read for over 30 seconds.
Changing the vehicle's identity or detection sources also invalidates
its grant. Deliberate pauses, locks and reached targets remain effective. Local
RFID authorization must be disabled for this EMS-based feature.

Enabling the feature can stop an ongoing charge while it waits for recognition.
Disabling it does not resume charging: explicitly enable **Charging release**
afterwards if you want to allow charging. HA's **Virtual Plug & Charge** sensor
shows whether recognition, permission or release is still missing.

This is a convenience check based on vehicle-integration signals, not ISO 15118
or cryptographic vehicle authentication. It needs app **0.8.0b1 or newer**;
ordinary service connections keep their lower minimum version.

## Updates

**The app does not need an update whenever the integration changes.** Their
version numbers may differ. Integrations 0.6.1 and 0.8.0b6 need **service 0.6.1
or newer with API 2** so timed commands expire in the service even while HA is offline.
Update older apps first, including 0.6.0b1. Future integration-only updates can
reuse a compatible service without restarting its heartbeat.

For the separate live Modbus **Debug log** tab and all beta app fixes, update
the app to **0.8.0b6**. Virtual Plug & Charge needs **0.8.0b1 or newer**. Changing
a vehicle percentage target also needs the confirmed-update capability included
since 0.8.0b1; HA requests an app update
if that capability is missing. Other controls remain available with an older
compatible service. The general minimum has not changed; it does not imply
that an older app already contains the new features or fixes.

Update the app when its own update is offered or the integration's release notes
require a newer service. Use **Check for updates** in the app store. **If both
components need updating, update the app first**, wait for initialization, then
update the integration and restart Core. **Updating the app itself briefly
interrupts the heartbeat.** Keep its data and the existing HA entry.

**From an older version:** update this app to **0.8.0b6 first**, then the
beta integration. The API 2 minimum introduced in 0.6.1 still applies; no
intermediate installation of 0.6.1 is needed. The old beta integration may show unavailable controls between
updates because its API differs. Keep the existing beta app and its data; no
reinstall is needed. If an older technical connection choice is rejected,
select its matching readable name as described above before starting the app.
The token, saved charging state and USB settings are retained; switching to LAN
is optional.

Stable **0.6.1** is offered through `#apps`; beta **0.8.0b6** is offered through
`#apps-beta`. Keep an existing beta app and update normally. Switching catalogs is optional and
creates a different app in Supervisor. For that change, back up the data, note
the charging settings and disconnect the vehicle. Copy the options, set the new
app to **Read-only**, stop the old app before starting the new one, and confirm
its discovery. Keep the old app and backup until the handover is verified.
The new app has separate saved state: note and verify pause, current and release
settings as in the first setup before reconnecting the vehicle.

## AppArmor protection

App **0.6.1** includes a custom AppArmor profile. Supervisor loads it automatically
when installing the app. Older stable 0.5.1 did not include this profile.
Keep **Protection mode on**. The profile allows USB/LAN communication, heartbeat
and the service's own token and saved charging state. The options file remains
read-only; the authenticated connection page saves through Supervisor's own-app
API. Adapter names are read locally without requesting host-wide hardware access.

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
| Token rejected | During setup, retry discovery confirmation. For an existing connection, HA first tries to refresh access automatically. If an authentication prompt remains in Devices & services, confirm it; no token entry is needed. See [renew service access](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/troubleshooting.md#renew-service-access). |
| Service API or minimum version is incompatible | Check the API and minimum service version required in the integration’s release notes. Version numbers do not need to match. Follow [Updates](#updates) if needed; changing the token does not fix this. |
| Controls unavailable | Check **Read-only**. After changing it, save and restart the app, then wait for fresh status. Check the USB/LAN connection if controls remain unavailable. |
| App stops immediately | The log's **Invalid or unreadable service configuration** message names the invalid setting or startup phase. |
| Connection setup required | Open **Web UI**, configure USB or LAN, save and restart the app. Setup mode is running but does not advertise ready wallbox data. |
| Web UI asks for Supervisor 2026.11.0 | Update the app to **0.8.0b6** and reopen the page. This beta accepts Supervisor **2026.10.4+**; the integration's HA minimum version is unchanged. |
| Serial adapter marked missing | Reconnect the saved adapter or choose its replacement from the dropdown. For LAN, change the connection type and save; the old serial path is removed. |
| Heartbeat or write fault | Download integration diagnostics before restarting the app, then check the log. A managed heartbeat or HA-to-app link failing continuously for 60 seconds raises a repair that clears on recovery; a connection fault has no Fix button. |

The log shows service startup, the USB/LAN connection and heartbeat start/stop.
Diagnostics include the last 20 failed write/recovery records in RAM, with timing
information and no packet contents or credentials. They add no Recorder rows and
are lost when the app restarts.

For optional live troubleshooting in **app 0.8.0b6**, open **Web UI → Debug log**
(German: **Debugprotokoll**) and enable capture. The separate **Connection** tab
(**Verbindung**) contains connection settings, preserving unsaved inputs when
you switch tabs. The app's native Supervisor **Configuration** tab remains
unchanged.

Existing reads, writes, retries and heartbeats appear with
register addresses, decoded values, duration and outcome. A failed write shows
the requested value, not proof that it was applied. Capture is off by default,
keeps at most **200 transactions in RAM** and automatically stops and erases
itself after **10 minutes**, including when the browser is closed. Turning it off
also erases entries; clearing entries does not extend the deadline. Switching
back to **Connection** or hiding the browser tab stops UI polling while capture
continues until its deadline. There are no extra Modbus requests,
Recorder entries or log files. Serial numbers and text registers are hidden;
network login traffic and exception messages are never captured. In setup-only
mode, first save a connection and restart the app.

Verify deliberate pause, HA restart, app restart and reconnection on your own
hardware. Error-200 recovery while paused and particular LAN gateways still need
confirmation on the actual hardware; automated tests do not establish that.

[Full setup, fallback and troubleshooting guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b6/docs/modbus-service.md)
