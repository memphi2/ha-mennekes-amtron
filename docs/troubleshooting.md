# Troubleshooting

Start with the symptom that matches your setup. **Direct** means HA connects to
the USB/RS-485 or LAN adapter. **Service** means the Modbus app owns that
connection and HA connects to the app's HTTP API.

| What went wrong? | Start here |
|---|---|
| Controls are missing or greyed out | [Controls and write access](#entities-are-missing) |
| App discovery asks for credentials or reports an error | [App connection and discovery](#app-connection-and-discovery) |
| The serial connection cannot find the wallbox | [Serial connection](#the-config-flow-cannot-connect) |
| The LAN adapter cannot find the wallbox | [LAN protocol, port and gateway settings](network-gateway.md#network-access-and-troubleshooting) |
| Heartbeat is unhealthy or error 200 appears | [Heartbeat recovery](#error-200-energy-manager-unavailable) |
| Charging does not follow the slider or pause switch | [Charging current](#the-charging-current-does-not-follow) |
| Sensors became unavailable | [Missing readings](#some-sensors-became-unavailable) |
| History keeps growing | [Database size](#the-database-keeps-growing) |

For installation, downloads and updates, see [Installation and updates](installation.md).

## Entities are missing

**Direct USB or LAN:** open the integration entry's options and set **Control mode**
to **Modbus master**. New direct entries start **Read only**, with readings but
without controls. Check the sensor readings and exclusive bus access first.

**Service:** disable **Read-only** in the app configuration, save and restart the
app. HA itself needs no restart for this switch. The app's switch is the only
write-permission setting. After a fresh, healthy
service status reaches HA, controls become available automatically. They keep
their entity IDs while read-only, disconnected or not ready; no second HA switch
is required. If you are upgrading from an old beta where they were missing,
follow [the beta upgrade steps](installation.md#updates). A routine integration
update does not require updating or restarting a compatible app.

If just one feature is missing, check the [entity reference](entities.md):
older register layouts provide fewer sensors, phase selection requires capable
hardware, and some diagnostics are disabled by default. Enable **Modbus layout
version** in its entity settings to compare your device with the reference.

Do not delete and re-add an existing wallbox to recover its controls. Using
**Reconfigure** or confirming its discovered app preserves the matched device
and entity IDs.

## Writes are refused

First check write access above. When controls are available, the message helps
distinguish a waiting interval from a device problem:

| Message or symptom | Next step |
|---|---|
| A remaining wait time is shown | Early changes are queued and shown in **Pending change** and **Pending change due**. |
| Service unavailable | Check the app log and its USB/LAN connection. HA reconnecting does not replay old commands. |
| Device rejected a write | Check satellite configuration, supported hardware/registers, device state and whether another master is using the bus. |
| Restart refused | The wallbox must report **idle** before its **Restart** button can be used. |

See [service setup](modbus-service.md) or the [hardware guide](hardware.md) for
the relevant connection.

## App connection and discovery

On Home Assistant OS, a running app supplies its connection details through
Supervisor. Confirm the discovered device under **Settings → Devices & services**;
you normally do not enter a URL or token. A separately hosted service uses manual
connection details; see [standalone setup](service-standalone.md).

The **Service URL** is our HTTP service, usually on port **8765**. A LAN-to-RS-485
gateway uses separate **host/TCP port** fields, default port **502**, and no
service token. Choose **Direct LAN adapter connection** for a gateway used by HA
itself, or configure those fields in the app when the service owns it.

| Message or symptom | What to check |
|---|---|
| No discovery, or the integration asks for URL and token | Confirm the app is installed and running, then check Devices & services again. Check the integration’s minimum service/API requirements; the app and integration version numbers may differ. If you have multiple Modbus apps, select the matching discovered app. |
| **App cannot be reached** | Check that the app stays running. For a separate service, also check its host, network and port 8765. |
| **Token invalid / authentication failed** | During setup, confirm discovery again or check the standalone token. For an existing entry, follow [Renew service access](#renew-service-access). Do not post tokens in an issue. |
| **Not ready** | Authentication succeeded, but usable wallbox data is not available yet. Check the app log for USB/LAN connection and initialization, then retry. Changing the token will not fix this. |
| **No serial number yet** | The app has not read the serial number needed to match the wallbox. Check the wallbox connection and supported register layout. |
| **Service API or minimum-version requirements are not met** | Integration 0.6.1 needs **service 0.6.1 or newer with API 2** for service-side expiry of queued timed commands, even if timed planning is unused. Check the requirements in its release notes. Different version numbers alone are fine. If both need updating, update the app/service first, then the integration and restart Core. Keep existing data and entries; changing the token will not fix this. |
| **App responds, but its data was not accepted** / `invalid service snapshot` | Copy the field named in parentheses in the HA log, such as `identity.max_evse_current`, and download diagnostics before restarting the app. Include both component versions. This is a data-validation error; differing release numbers or a bad token are not implied. |
| **Different wallbox / identity cannot be confirmed** | Check the Modbus address, gateway route and service configuration; see [wallbox identity](#a-different-wallbox-is-reported). |
| App repeatedly stops, or reports **invalid or unreadable config** | Read the app log from startup and check its configuration against the [service reference](service-reference.md). If no app log is available, check the Supervisor log for its exit reason. A serial device that is temporarily absent is normally retried without stopping the app. |
| Older app **0.5.2b2** rejects LAN settings because an old USB device no longer exists | Update to **0.6.1**, which includes the fix for this validation conflict. Before updating, clear the saved **Serial adapter** selection in the native Configuration tab, then save LAN settings and restart. LAN needs no local USB device. |
| **Connection setup required** | Open the app's **Web UI**, choose USB or LAN, save and restart. Until then, the app runs only setup and health checks, without Modbus, heartbeat or discovery. |
| Web UI requests a Supervisor update | The guided connection page requires an administrator and stable **Supervisor 2026.10.4+**. The native Configuration tab remains available; see [connection setup](upgrading.md#app-connection-page). This does not change the integration's HA minimum version. |

An app that starts successfully can still be waiting for the wallbox. The log
separates API startup, USB/LAN connection, heartbeat start/stop and shutdown.
Check the last completed step instead of repeatedly restarting everything.

### Renew service access

If an existing connection rejects authentication, Home Assistant starts access
renewal. For a Supervisor app, it first tries the current credentials
automatically. If a prompt remains, open it under **Settings → Devices &
services** for the existing **Mennekes Amtron RTU** entry:

- **Home Assistant OS app:** confirm the prompt. HA retrieves fresh credentials
  from Supervisor; there is no token to copy. If retrieval fails, check that the
  app and Supervisor are running, then try again. Restart the app only if needed
  to restore it; restarting the app briefly interrupts its heartbeat.
- **Separately hosted service:** enter the service's current access token. Its
  saved URL stays unchanged. To move to another URL, use **Reconfigure** instead.

The existing service URL stays unchanged. If the entry was identified by a serial
number, the service must report the same one. For entries added without a readable
serial number, HA can retain the URL but cannot independently confirm the physical
wallbox's identity.

The device, entity IDs and options are kept; do not delete and add the integration
again. A successful confirmation reloads the integration without restarting HA.
HA does not replay old charging commands. The independent service can still
recover heartbeat error 200 using its saved charging state, including a pause.
Direct USB and LAN connections have no service token and do not use this flow.

## The config flow cannot connect

For direct USB, check the adapter and bus before starting a broad search. For
LAN, follow the [gateway checks](network-gateway.md#network-access-and-troubleshooting);
there is no serial-parameter search over the network.
The same bus checks apply to the app's serial connection:

1. Confirm that the process using the adapter can open it. For a virtual machine
   or container, check USB/device passthrough and permissions. Prefer a stable
   `/dev/serial/by-id/...` path.
2. Stop any competing connection. Only one integration, app, serial tool or
   energy manager may use the bus. **Read-only and discovery still send requests.**
3. Confirm the address and serial settings. Factory defaults are address **50**,
   **57600 baud, 8 data bits, no parity, 2 stop bits**.
4. Have the installer check satellite mode and wiring against the
   [hardware guide](hardware.md) and manufacturer instructions.

The direct setup form can search addresses at the chosen settings or search all
documented baud rates and frames. A full search can take around 15 minutes or
longer. It does not write registers or send a heartbeat, but needs exclusive bus
access. Reconfiguring a live direct entry stops its connection temporarily, so
its heartbeat is also interrupted. Failed or cancelled searches restore the old
entry.

No result does not prove a wiring defect. Port permissions, another process,
settings and device mode can also prevent a connection. The
[optional bus check](quickstart.md#optional-command-line-bus-check) helps separate
serial/device problems from HA setup.

## The device answered but reported no layout version

A response alone does not confirm supported hardware. Check the model against
[device compatibility](compatibility.md), then check satellite configuration and
the address. Another device may be answering at that address.

An older supported layout can work with fewer features; it is different from a
response with no usable layout version. Include diagnostics or redacted bus-check
output when reporting an unrecognized device.

## A different wallbox is reported

If your entry was paired with a serial number, the connection must report that
same serial number. A different or missing number keeps controls unavailable.
Check the Modbus device address, gateway route and service configuration. Do not
delete the entry to bypass the check; it recovers when the configured wallbox
can be confirmed again.

Entries originally added without a readable serial number remain bound to their
connection endpoint. They cannot independently verify the physical wallbox's
identity. A successful network connection alone does not prove the right device
is behind it.

## Heartbeat or service connection repair

After **60 seconds of continuous failure**, a managed heartbeat can raise a
repair even when the wallbox error register cannot be read. Brief interruptions
do not immediately create a repair. No such heartbeat repair is raised for
read-only operation or an unmanaged heartbeat.

- **Heartbeat unavailable:** check the USB/LAN connection, adapter power, bus
  ownership and app/integration logs. Delivery is unhealthy; the wallbox's
  fallback may apply.
- **Service connection unavailable:** HA cannot reach the app. The independent
  app may still be sending its heartbeat and keeping the last charging state.
  Check its status and local network before assuming charging has stopped.

These connection repairs clear automatically after recovery. They have no
**Fix** button because a wallbox recovery command cannot repair a lost
connection. A confirmed wallbox error 200 uses the separate repair below, so
both messages are not shown for the same confirmed firmware fault.

## Error 200, "Energy manager unavailable"

The wallbox did not receive a usable heartbeat in time. With writes enabled,
recovery runs automatically after heartbeat delivery resumes and a fresh reading
confirms error 200. The direct integration or independent service performs the
recovery, depending on your connection.

Recovery keeps a deliberate pause, device lock, disabled charging release and
current setting, including unlimited mode. It does not turn a deliberate pause
into charging. There are at most three attempts, spaced 30 seconds apart while
the fault persists. A confirmed healthy state resets the retry budget. Remove
an older recovery automation if it is making duplicate attempts.

The repair disappears after a reading confirms the error has cleared. If
recovery cannot complete, use **Recover from error** or the repair's **Fix**
action. The dialog waits up to ten seconds for confirmation while the heartbeat
continues. It stays open if the fault persists, a different error appears or the
connection fails. Use the pause/release controls explicitly when you want to
resume a deliberately stopped charge.

If the error returns, check what interrupted the heartbeat:

- **Direct USB or LAN:** HA Core restarts, entry reloads and read-only mode stop it.
- **Service:** an HA Core restart leaves it running. An app shutdown, app
  read-only mode, host restart or USB/LAN failure can interrupt it.
- **Either connection:** network/gateway outages, bus conflicts, USB disconnects, wiring problems, slow
  responses or delayed processing can make heartbeat delivery fail or arrive late.

The [optional Modbus service](modbus-service.md) is recommended for uninterrupted
heartbeat during Core restarts. It cannot protect against its own shutdown or a
host failure. Configure an intentional
[energy-manager fallback](safety.md#the-heartbeat-and-what-happens-without-it)
with the manufacturer's tool; the integration raises a repair if fallback is
disabled.

### An occasional “No response received after 0 retries”

A missing acknowledgement does not prove the wallbox missed the command. If
charging continued normally, check the next heartbeat and download diagnostics
before restarting the app or reloading the integration.

For a request ending in `32 06 0d 00 55 aa 31 8a`, the missing acknowledgement
belongs to a heartbeat to wallbox address 50. Zero write retries are intentional:
repeating an unconfirmed control command could apply it twice. Heartbeats have
their own shorter retry interval. The empty `extra data:` field is not evidence
of damaged packets.

In `write_transactions`, a high `queue_wait_ms` means the request waited inside
the client. `tx_packets: 1` with `rx_callbacks: 0` means no receive callback was
observed during that transaction. From 0.5.2b1, these counters and timings
also include any preceding identity check; a sent packet alone does not prove
that the actual write was sent. Received bytes do not necessarily form a valid
response. `io_error` identifies a library I/O error, not its physical cause; a
later `recovered` entry confirms a successful write to the same register.

These are software timings, not measurements on the wire. They cannot alone
distinguish delayed processing, USB/RS-485 trouble or a slow wallbox. See
[diagnostics](#diagnostics-for-intermittent-problems) below.

## Some sensors became unavailable

A failed group of register reads makes the affected entities unavailable until
a successful retry. Other readings can keep working. If everything disappears,
check the connection or app first. If only one group repeatedly fails, include
the diagnostics field `failed_blocks` when reporting it; the range may reveal a
firmware-specific limitation.

## The charging current does not follow

| Observation | Check |
|---|---|
| The slider is at 0 A, but charging continues | **0 A removes the limit.** Turn **Charging paused** on to stop. |
| The slider will not accept 1–5 A | Use 0 A for no limit or at least 6 A for a limit. Pause has its own switch. |
| A new current is delayed | Current writes have a five-second minimum interval. The latest accepted request is applied after that interval. |
| Pause/resume is refused just after startup | Early pause and resume requests are queued for the configurable state-change interval and five-second write floor. An HA Core restart does not reset a running service's timing. |
| The slider maximum is lower than expected | Check the device maximum and HA current cap. In service mode, the app's current cap also applies. |
| Offered current is lower than the slider setting | Installation limits and an active downgrade can lower **Signaled current**. The vehicle can draw less again. |
| A current change waits while paused | Setting a current can resume charging. Inspect **Pending change** and **Pending change due** for a queued resume. |
| Charging continues after requesting pause | The pause shares the state-change interval and the five-second current-write floor. Check **Pending change** and **Pending change due**: the pause replaces an older pending start and runs when permitted. Release-off and lock-on also wait; switching controls does not bypass the interval. |
| The slider still shows a value during a pause | It shows the remembered resume setting. Check **Charging paused**, **Signaled current** and **Total power** for actual charging. |
| A manual pause is undone later | Check automations that write the current or pause switch. Disable the PV pause/resume automation if the manual pause should remain. |
| A pending change disappears after restart | A restart of the bus owner cancels queued commands. Request the action again if still wanted. HA Core restarting alone does not cancel a running service's queue. |
| A pending change disappears without changing the wallbox | A newer charging command or stop can replace/cancel it. If no new command was sent, inspect connectivity, write permission and the integration/app log for an execution failure. |

## PV automation stopped after an update

Open the **Charge from PV surplus** automation and select its **Charging
paused** input. The blueprint sends no commands until that input is configured.
Ensure the entities belong to the same wallbox and the power sensor provides a
numeric value in **W or kW**.

See [Blueprint updates](blueprints.md#updating-existing-blueprint-automations)
for edited copies and input changes. Inspect the automation trace for failed
triggers or conditions. HA restarts and automation reloads reset threshold hold
timers.

## Phase switching leaves charging paused

This is expected when switching during charging. Resume explicitly after the
phase change has actually run. The integration uses the configurable state-change interval and queues early
requests. Check **Pending change** before requesting resume: a new resume
request replaces a phase change that is still waiting. Check your vehicle's
support; some vehicles abort the session after a phase change. The selector
exists only on capable wallbox hardware.

Since 0.5.2b2, the phase command is sent only after live status confirms
that the vehicle has paused. If confirmation fails or times out, the requested
pause remains and no phase command is sent. Check the wallbox connection and
vehicle state before trying again; a pause command being accepted does not by
itself confirm that the vehicle has stopped charging.

While waiting for that confirmation, **Pending change** still shows the requested
phase action but **Pending change due** is unknown. This is expected: the vehicle
determines when confirmation arrives. Once confirmed, the due time shows the end
of the configured extra wait. An idle wallbox with no vehicle skips confirmation
and that extra wait.

## The database keeps growing

A slower measurement interval reduces state-history traffic, but does not
reduce the number of long-term statistics series. Long-term statistics survive
normal history purges. Use the [Recorder profiles](recorder.md) to reduce new
detail history and statistics while retaining total energy for the Energy
dashboard.

## Diagnostics for intermittent problems

Download **diagnostics** from the integration entry menu before restarting or
reloading. The connection and write history is held in memory and resets when
its owning process restarts; it adds no Recorder rows.

| Field | What it contains |
|---|---|
| `runtime_events` | Up to 40 connection changes, heartbeat failures/recoveries, late heartbeats, rejected writes and recovery attempts. Times are seconds since entry load or service startup. |
| `write_transactions` | Up to 20 failed writes and the first subsequent confirmation for each affected register. Includes queue/transaction durations and send/receive callback counts. Ordinary successful writes do not add entries. |
| `failed_blocks` | Register groups whose latest read failed. |

Service event/write history is fetched only when downloading diagnostics; it is
not included in regular status polling. The stable 0.5.0 service provides these
fields; an integration-only update does not require an app restart to retain
them. The histories omit packet contents, write values and arbitrary exception
messages. Diagnostics omit identifying register fields, the serial number and
full serial-port path.

## Getting help

Include the integration, app (if used) and Home Assistant versions; wallbox
model/firmware; direct or service connection; read-only setting; expected
behavior and exact error. Attach diagnostics and the relevant log messages.
Review extra logs before posting: remove tokens, serial numbers, private
hostnames and adapter identifiers.
