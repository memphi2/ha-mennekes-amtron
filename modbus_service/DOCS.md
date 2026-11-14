# Mennekes Amtron Modbus Service

Keep the wallbox heartbeat running through Home Assistant Core restarts.
This optional app is the preferred way to operate the integration: it owns the
USB or LAN connection, sends the heartbeat and holds the last charging state,
including a deliberate pause, while HA is offline. It can also run a charging
order on its own.

**amd64 and aarch64 · separate integration required**

LAN-to-RS-485 adapters are supported without local USB. See the
[LAN guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/network-gateway.md)
for transparent RTU over TCP versus translating Modbus TCP. Supported Waveshare
models also offer optional [LAN adapter setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/lan-adapters.md#service-app).

[First setup](#first-setup) · [Web pages](#web-pages) · [Daily use](#daily-use) ·
[Updates](#updates) · [Help](#help)

## First setup

Install the **Mennekes Amtron RTU** integration in the same version as this app
and complete any requested Home Assistant Core restart. See
[Installation](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/installation.md).

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

| App option | Initial setting |
|---|---|
| Wallbox connection | **USB / RS-485** (default), **LAN: Modbus RTU (TCP)** (transparent adapter), or **LAN: Modbus TCP** (translating gateway). |
| Serial adapter | USB only: choose it on the **Connection** page; stable `/dev/serial/by-id/...` names are preferred. Saving LAN removes the old USB selection. |
| Adapter host / IP + TCP port | LAN: hostname/IP without a URL scheme, and gateway port; default **502**. |
| Wallbox address | Match the wallbox; factory default **50**. |
| Baud rate / frame | USB only: match the wallbox, factory defaults **57600 / 8n2**. For LAN, set these on the adapter. |
| Read-only | **On** until readings are checked. |
| Current cap | Default **6 A**; set within your installation's permitted current. |
| Status / measurement interval | Keep **5 / 30 seconds** initially. |

**Minimum time between state changes** defaults to **300 seconds**, range
**0–3600 seconds**. Zero disables the longer wait; the five-second write guards
remain. HA does not add a second interval.

**Extra wait before phase changes** defaults to **5 seconds**, range **0–30
seconds**. After the vehicle confirms a pause, the app waits this extra time and
checks the state again before changing phases. Zero skips only the extra delay.

With no vehicle connected, fresh EVSE Idle and CP A1/A2 readings skip the long
state-change wait and the vehicle grace time. Pending waits check for unplugging
every five seconds. Unknown or inconsistent status keeps the wait.

Leave the app's API settings at their defaults: HTTP port **8765** is separate
from a LAN adapter's Modbus port. No host port needs publishing and no service
token needs configuring. Use a trusted private LAN for gateways. Only this app
may use the bus through its USB or LAN adapter, even when read-only.

On first installation, the app reads the wallbox's current state. If the
fallback changed that state during handover, it cannot infer your earlier
intention. A paused wallbox without saved app state uses **6 A** as its first
resume target.

## Web pages

**Open Web UI** opens the app's pages for Home Assistant administrators. They
need **Supervisor 2026.10.4 or newer**. See
[the app's web pages](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/app-guide.md)
for details.

| Page | Purpose |
|---|---|
| **Overview** | Which controller decides about the charge, why the car waits and what comes next; observed charging state, heartbeat, power, the next queued action, recent command results and every register with raw and decoded value. Read-only. |
| **Connection** | USB or LAN connection and optional LAN-adapter setup. Changes apply after an app restart. |
| **Log** | The Modbus traffic as it happens, in memory only. Runs while the page is open. |
| **Runtime** | Polling intervals, current cap and waits apply without restart. Preview before saving. |
| **Charging jobs** | One order for the plugged-in car: time window, current and additional energy, with an optional immediate reserve, early start and an estimated finish. |
| **Guest charging** | An immediate, limited order for a visitor's car after local authorization. |
| **Weekly plans** | Recurring departures for one car, activated for the plugged-in car for up to seven days. |

Every page is available in English, German, French, Dutch and Italian and has
the same navigation bar.

**Read-only** and the basic connection fields stay in the native
**Configuration** tab. Avoid editing the native tab and a web page at the same
time; the page asks you to reload when the settings changed meanwhile.

### Connection page

The page offers exactly three connection choices and shows only the fields
needed for the selected one. A disconnected USB adapter is marked missing;
another adapter is never selected automatically. Reconnect it or choose its
replacement explicitly. Switching to LAN does not need the old USB adapter.

Optional **LAN adapter setup** inspects only the host you enter. Select **Read
LAN adapter settings**, review the proposed changes, select **Apply adapter
settings when saving** and save. This selects TCP-server mode and turns off
storage polling, autonomous preambles and old serial-buffer replay; unknown
custom payloads are refused. Applying restarts the adapter and closes the app's
Modbus connection immediately; restart the app afterwards. Reading alone changes
nothing. See [supported models, TCP login and recovery](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/lan-adapters.md).
The native TCP-login switch changes the app connection only; it does not enable
login on the adapter.

### Charging jobs and guest charging

A charging job runs inside the app, so it continues while Home Assistant
restarts or is offline. It charges with the chosen current inside its window
and pauses outside it, after the energy amount or when you stop it. It never
switches the charging release on, unlocks the wallbox or authorizes a car;
battery and energy targets in HA still stop it.

While an order runs, HA cannot change the current, resume or switch phases; it
reports that a charging job of the app controls charging. Pausing, switching the
release off or locking in HA stops the order. When an order ends, the app pauses
charging; once the wallbox confirms the pause, HA controls charging again and
you can resume there. Unplugging interrupts an order. After an app restart or a
wallbox that was unreadable for more than a minute, the order stays paused and
continues only if the wallbox proves the car stayed plugged in; otherwise it is
interrupted and HA controls charging again. It never restarts by itself.

A weekly plan creates one such job per departure, each when its window opens.
Between departures HA controls charging, for example for PV surplus. An
activation ends at unplugging, an app restart or after seven days at the latest.
A plan activated while HA had detected the car at the plug-in remembers it and
then activates itself whenever HA detects that car again, until you stop the
plan. It then waits for the next departure's window.
See
[the app's web pages](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/app-guide.md#weekly-plans).

## Daily use

- **Pause/resume:** use **Charging paused** in HA.
- **Current limit:** use the HA slider. **0 A removes the EMS limit; it does not
  pause charging.** The app and HA caps constrain limited-current commands;
  the wallbox's hardware limits apply even at 0 A.
- **Write permission:** change the app's **Read-only** switch, save and restart
  the app. No HA restart is needed. HA controls remain registered but unavailable
  while read-only or while the app is not ready. HA does not send old commands
  when permission changes. The app can still recover error 200 by reasserting
  its saved state, including a deliberate pause.
- **Heartbeat status:** check the `heartbeat` attribute of HA's **Connection
  quality** diagnostic sensor, or the **Overview** page.
- **Charging targets:** set an **Energy target** in kWh, or configure an optional
  vehicle percentage target through the integration. Targets only limit charging.
  See [target behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/user-guide.md#energy-target).
- **Next departure:** set a charging deadline for one trip in HA. This optional
  plan may resume a pause to meet the time; ordinary kWh or percentage limits
  never do. HA hands the trip and the car's data to the app, which plans start
  and current itself and keeps doing so while HA is offline.
  See [departure planning](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/departure-planning.md).
- **Loss and cost estimates:** choose optional vehicle and price sources in the
  integration options. See [charging estimates](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/charging-estimates.md).

Early pause/resume, phase, release and lock changes are queued. HA's **Pending
change** and its `due` attribute show the request and its scheduled time. The
latest charging request replaces the old one. **A stop request also waits**,
replacing any pending start. A pause within the five-second current-write
interval is queued too and does not need to be requested again. While a phase
change waits for the vehicle to confirm its pause, the action stays visible but
the due time is unknown. Pause, release, lock and phase readback always show the
actual wallbox state; while paused, the slider shows the remembered resume
current.

Before changing phases, the app waits up to 30 seconds for live confirmation
that the vehicle has paused, followed by the configured extra wait. If either
check fails, the phase command is not sent and the pause remains. Resume
separately after a successful phase change. An idle wallbox without a vehicle
needs neither confirmation nor the extra wait.

HA can remain offline indefinitely while the app holds the last charging state.
Automations cannot produce new settings during that outage. The app still runs
a command queued before HA stopped and continues a handed-over departure plan;
without new charge levels from the car it projects them from the metered
energy. Restarting the app cancels pending commands but keeps the accepted
charging state; the departure plan continues when the wallbox proves the car
stayed plugged in. **App, host, power or USB/LAN/gateway failures
still interrupt the heartbeat** and the wallbox's configured fallback applies.
Switching the app to read-only also stops the heartbeat.

To give the bus to another master, stop the app. Disabling the HA entry or
selecting read-only does not release it. Include the app data in backups: it
contains the saved charging state, a charging job, weekly plans, learned
charging power and the private access token.

### Virtual Plug & Charge

This feature is **optional and off by default**. Configure vehicle profiles and
fresh cable/home detection in each car's profile in HA, then switch on
**Virtual Plug & Charge** under **Configuration** on the wallbox device page.
That switch is the only permission: every profile with detection signals takes
part. Automatic selection is chosen in the **Active vehicle** entity; selecting
a profile manually does not replace the detection check. See the
[vehicle setup guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/vehicle-setup.md#optional-virtual-plug--charge).

The app withholds charging release until HA recognizes the selected vehicle
unambiguously. A grant is valid only for the current physical connection. The
app keeps it through HA Core restarts, even if cloud vehicle signals later
become unavailable. Unplugging or restarting the app clears it; a new grant
needs fresh recognition from HA. A briefly unreadable or contradictory wallbox
state keeps the grant. After 60 seconds of unknown state the release is switched
off, and HA grants the same connection again once the car is reported
connected, without replugging. The log names the reason whenever a grant ends.
Changing the vehicle's identity or detection sources also ends its grant.
Deliberate pauses, locks and reached targets remain effective. Local RFID
authorization must be disabled for this feature.

Enabling the feature can stop an ongoing charge while it waits for recognition.
Disabling it does not resume charging: switch **Charging release** on explicitly
if you want to allow charging. HA's **Virtual Plug & Charge** sensor shows
whether recognition, permission or release is still missing.

This is a convenience check based on vehicle-integration signals, not ISO 15118
or cryptographic vehicle authentication.

## Updates

Update this app and the integration together. **Update the app first**, wait
until it has started, then update the integration and restart Core. **Updating
the app briefly interrupts the heartbeat.** Keep its data and the existing HA
entry; do not reinstall the app or add the wallbox again.

Stable releases are offered through `#apps`; prereleases through `#apps-beta`,
which also receives stable releases. The two catalogs create different apps in
Supervisor. To change catalogs, back up the app data, note the charging settings
and disconnect the vehicle. Copy the options, set the new app to **Read-only**,
stop the old app before starting the new one and confirm its discovery. The new
app has separate saved state: verify pause, current and release as in the first
setup before reconnecting the vehicle.

## AppArmor protection

The app ships its own AppArmor profile, which Supervisor loads when installing
the app. Keep **Protection mode on**. The profile allows USB/LAN communication,
the heartbeat and the app's own files: token, saved charging state and charging
job. The options file is read-only; the web pages save through Supervisor's
own-app API. Adapter names are read locally without host-wide hardware access.

With default settings, a rating of **8/8** is possible after the profile has
loaded; **7/8 before installation** is normal. The rating is not proof of
hardware testing. See [Supervisor's rating rules](https://contributors.home-assistant.io/apps/reference/security-rating/).

If AppArmor denies an operation, check the Supervisor log and the host kernel
log for `apparmor="DENIED"`. Report the operation and path with private details
removed, and keep protection enabled while investigating.

## Help

| Symptom | What to check |
|---|---|
| No discovered entry | Confirm the app is running. In HA, choose **Add integration → Mennekes Amtron RTU → Connect to the Modbus service** to look it up again. |
| Not ready | Authentication succeeded, but wallbox data is unavailable. Check the app log for USB/LAN connection and initialization errors, then retry. No token change is needed. |
| Token rejected | During setup, confirm discovery again. For an existing connection, HA refreshes access automatically; confirm a remaining prompt in Devices & services. See [renew service access](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/troubleshooting.md#renew-service-access). |
| Service API or version incompatible | Update the app and the integration to the same release, app first. Changing the token does not fix this. |
| Controls unavailable | Check **Read-only**. After changing it, save and restart the app, then wait for fresh status. Check the USB/LAN connection if controls stay unavailable. |
| HA reports that a charging job of the app controls charging | A charging job or guest order runs for the plugged-in car. Stop it on **Charging jobs**, or unplug the car; HA controls charging again once the pause is confirmed. |
| App stops immediately | The log's **Invalid or unreadable service configuration** message names the invalid setting or startup phase. |
| Connection setup required | Select **Open Web UI → Connection**, configure USB or LAN, save and restart the app. |
| The web page asks to update Supervisor | Update Supervisor to **2026.10.4 or newer**, or use the native **Configuration** tab meanwhile. |
| Serial adapter marked missing | Reconnect the saved adapter or choose its replacement from the dropdown. For LAN, change the connection type and save; the old serial path is removed. |
| Heartbeat or write fault | Download integration diagnostics before restarting the app, then check the log. A heartbeat or HA-to-app link failing continuously for 60 seconds raises a repair that clears on recovery. |

The log shows service startup, the USB/LAN connection and heartbeat start/stop.
Diagnostics include the last 20 failed write/recovery records in RAM, with timing
and no packet contents or credentials. They add no Recorder rows and are lost
when the app restarts.

For live troubleshooting, open **Log** (German: **Protokoll**). It starts by
itself and shows the app's own reads, writes, retries and heartbeats with
register addresses, decoded values, duration and outcome, one line each. A
failed write shows the requested value, not proof that it was applied. The
app records only while the page is open and at most **10 minutes** after it
was closed, and keeps at most **200 transactions in RAM**. It adds no Modbus
requests, Recorder entries or log files. Serial numbers and text registers are
hidden; network login traffic and exception messages are never captured. The
**Overview** shows the last value and read quality of every register.

Verify deliberate pause, HA restart, app restart and reconnection on your own
hardware. Automated tests do not replace that check.

[Full setup, fallback and troubleshooting guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/modbus-service.md)
