# Independent Modbus service

**0.5.0b2 is a beta for installation and hardware testing.** The integration
and service use the same version. Existing stable installations keep direct
serial mode until you explicitly switch them to the service.

The service owns the USB RS-485 adapter and keeps the heartbeat running while
Home Assistant Core restarts. Home Assistant connects to its authenticated API;
it no longer opens the serial port. This uses the same separation of hardware
service and HA integration as Z-Wave JS, with an HTTP API instead of WebSocket.

**A Home Assistant outage keeps the last charging state indefinitely.** A pause
stays paused, a disabled charging release stays disabled, and the last current
setting remains active. HA automations cannot calculate new values while HA is
offline. Reconnecting HA reads the service state and sends no charging commands.

A host restart, service restart, power loss or USB fault can still interrupt the
heartbeat. The wallbox's configured fallback applies in that case. See
[Safety](safety.md#the-heartbeat-and-what-happens-without-it).

## Install the beta on Home Assistant OS

Install the **0.5.0b2** integration release first. On HA 2026.11+, enable the
repository's **Pre-release** switch in the Marketplace; on HA 2026.9/2026.10,
enable beta releases in HACS. The service is an additional HA app installed
through Supervisor. HACS and the Marketplace install only the integration.

[![Add the Mennekes Amtron beta app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmemphi2%2Fha-mennekes-amtron%23apps-beta)

Alternatively, under **Settings → Apps → Install app → Repositories**, add:

```text
https://github.com/memphi2/ha-mennekes-amtron#apps-beta
```

Install **Mennekes Amtron Modbus Service**. Supervisor downloads the verified image
for amd64 or aarch64 and creates the local app image. The installation may show
**Building**; it does not compile Python or install dependencies on your device.
Enable **Start on boot** and **Watchdog**. The app starts
independently of Core. Its watchdog checks process liveness; a wallbox connection
fault is retried without repeatedly restarting the app.

Configure:

- **Serial adapter:** the stable adapter path, preferably `/dev/serial/by-id/...`.
- **Wallbox address**, **baud rate**, **serial frame:** the existing wallbox settings.
- **Read-only:** initially enabled; disable it when the service should maintain
  the heartbeat and accept charging commands.
- **Current cap:** the service's EMS current cap, from 6 to 32 A. The default is
  6 A. Stay within the installation's permitted current.

Selecting **0 A** on the integration's current slider explicitly removes the
EMS limit, including its extra current cap; the wallbox's hardware limit remains.
Use **Charging paused** to stop charging.

The app manages its own private access token. There is no manual token option;
Home Assistant receives the credential through Supervisor discovery. When
upgrading from beta 1, update the integration first: the app replaces the old
manually configured credential and HA learns the new one automatically.

The API uses port 8765 on the app's internal network. Host port publication is
disabled by default. After starting the app, open **Settings → Devices & services**
and configure the discovered **Mennekes Amtron** entry. Supervisor supplies its
address and token; there is no service URL to type. Discovery is restored after
HA Core restarts. If the wallbox is not ready yet, check the app log and retry
the discovery confirmation. Standalone Docker/Python services still use manual
URL and token setup.

Selecting **Add integration → Mennekes Amtron RTU → Connect to the Modbus service**
also looks up the running Supervisor app before showing a manual form. If more
than one app is announced, use the matching discovery card. Setup distinguishes
an unreachable API, rejected authentication, unavailable wallbox data and an
invalid response. For rejected authentication, restarting the app announces its
current credentials again. Invalid responses name the affected data section in
the HA log without printing tokens or response contents.

The integration's service options control only HA's access and refresh interval.
Read-only there disables HA commands while the service keeps its heartbeat and
charging state. Set Modbus polling/measurement cadence and the service's write
permission in the app configuration. HA's current cap can further restrict
limited-current commands; it cannot raise the service's cap. On resume, the
service applies HA's cap to its latest remembered setpoint while holding the
current-write lock. An explicitly selected unlimited setting remains unlimited.

The beta and stable catalogs use `#apps-beta` and `#apps`, respectively. They
are different apps to Supervisor. When switching channels, copy the options,
stop the old app before starting the new one, and confirm the newly discovered app.
Keep a backup and the old app until the handover is verified.

## Standalone container or Python service

Copy the **standalone container reference** from the
[published beta catalog](https://github.com/memphi2/ha-mennekes-amtron/tree/apps-beta).
Check that its release version matches your integration. Use the complete
`ghcr.io/memphi2/ha-mennekes-amtron-service@sha256:…` reference, including the digest,
as your Docker image. It selects the verified release even if a registry tag is
later changed. The same reference supports Linux amd64 and arm64. Save it with
your configuration; choose a new matching reference deliberately when updating.

Create a private data directory containing `options.json`. Its structure is:

```json
{
  "serial_port": "/dev/ttyUSB0",
  "device_id": 50,
  "baudrate": 57600,
  "frame": "8n2",
  "token": "REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS",
  "read_only": true,
  "current_cap": 6,
  "listen": "0.0.0.0",
  "port": 8765
}
```

Map that directory to `/data`, map only the required serial device, and attach
the service and HA containers to the same private network. Give the service a
restart policy independent of HA. No privileged container or host networking
is needed. The service itself uses plain HTTP: use a trusted private network,
or an HTTPS reverse proxy for connections outside that network. Redirects are
not followed and credentials must not be embedded in the URL.

Alternatively, with Python 3.14+, install this checkout into its own virtual
environment using `pip install .`, then run:

```bash
amtron-modbus-service --config /path/to/options.json --state /path/to/intent.json
```

The installed package contains only the driver/service and its dependencies;
Home Assistant is not a service dependency. Run it under an independent process
manager with access to the serial device and state directory.

## Move an existing wallbox

1. Note the current pause, current limit and charging-release settings, including
   the resume current if paused. Disconnect the vehicle for the first handover:
   the existing heartbeat stops while the adapter changes owners, and the
   wallbox may apply its fallback before the new service can read its state.
2. Disable the existing HA integration entry so it releases the serial port.
   Do not delete the entry: it retains the device and entity IDs.
3. Start the service with the same bus settings and **Read-only** enabled.
4. For the Supervisor app, confirm its discovered entry under **Devices & services**.
   The matching wallbox entry is reused automatically. For standalone services,
   reconfigure the existing integration, choose **Connect to the Modbus service**
   and enter the URL and token. The serial number must match the existing
   entry. Re-enable the entry if it remains disabled and compare its readings
   with the wallbox while the service is still read-only.
5. Configure the service's current cap, disable its **Read-only** option, and
   set the integration's **Control mode** to **Modbus master** with the desired
   HA current cap. These are separate write permissions. Reapply and verify the
   noted pause, current and charging-release settings before reconnecting the
   vehicle. The service first reads the wallbox's current state; it cannot infer
   an earlier HA intention from a changed fallback readback. A currently paused
   wallbox without saved service intent uses 6 A as its first resume target.
6. Restart **Home Assistant Core** and verify that the service remains running,
   its heartbeat stays healthy and the selected pause/current is unchanged.

Do not start another serial integration or energy manager on the same bus.
Removing or disabling the HA entry does **not** stop the independent service.
To relinquish control, stop the service or configure its read-only mode and
check the wallbox's fallback behavior.

## State, diagnostics and failures

The integration's diagnostics download fetches the app's bounded
`write_transactions` history on demand. It records failed writes and the next
confirmation per affected register, including time waiting for the bus and
send/receive callback timing. It remains in RAM only, resets when the app
restarts, and adds no Recorder entries or regular polling traffic. See
[isolated heartbeat timeouts](troubleshooting.md#an-occasional-no-response-received-after-0-retries).

The app log reports process start, accepted bus settings, API readiness, serial
connection, heartbeat start/stop and clean shutdown. Heartbeat acknowledgement
and connection failures are logged on state changes, not on every beat or poll.
Read-only mode explicitly reports that its heartbeat is disabled. Tokens and
wallbox/adapter serial numbers are not included in these lifecycle messages.

If the app stops immediately, look for **Invalid or unreadable service
configuration**: the message identifies the startup phase and invalid option.
A missing or disconnected serial device is retried while the API stays running.
An unexpected worker exit is reported and closes the serial connection; a fatal
process failure reports its error class and errno. Supervisor's Watchdog can
restart the app after a process failure, but repeated failures need their logged
cause corrected.

The heartbeat writer is supervised separately from polling. If it unexpectedly
exits, the service closes down so Watchdog can restart it; a working API can no
longer conceal a permanently stopped heartbeat. A normal missing response is
retried by the heartbeat task and does not trigger this process restart.

Only pause intent, charging-release intent and the resume current are saved in
`intent.json`, bound to the wallbox identity. The file is atomically replaced
with owner-only permissions when those settings change. It contains no growing
history. Both the file and its directory are synced before a save is considered
complete. Heartbeats and polls do not create state-file entries or Recorder rows.
Include the app's data directory in backups; protect its options file/token.

The service enforces the same current-write floor, phase-switch guards, device
limits and bounded error-200 recovery as direct mode. All clients share one
serialized command path. Failed or timed-out commands are never automatically
replayed by the HA client, because the write may already have reached the box.
A persistent recovery failure stays visible. Clearing error 200 while paused
still needs confirmation on the actual wallbox firmware.

At most eight API bus requests can be in flight; excess requests fail promptly
instead of building an unbounded command queue. Heartbeats retain priority over
bus reads and commands. Normal state requests read the cache without bus traffic.

The API exposes named actions and an allowlist of diagnostic reads, with bearer
authentication and version checking. It offers no arbitrary register-write or
remote-shutdown endpoint. `/health` reveals process liveness only. If the service
or wallbox becomes unreachable, HA marks its readings unavailable; reconnecting
does not replay HA restore values.

## Build a local development checkout

To test source changes without using the published image:

```bash
python scripts/build_service_app.py --output /tmp/mennekes-amtron-app
```

Copy this directory to HA's local app directory and install the local app.
The export includes the source and development Dockerfile so Supervisor
builds the checkout. Alternatively run `docker build -t amtron-modbus-service:local .`
from the repository root. A local app and an app from the published catalog are
different installations; stop the current serial owner before switching.
