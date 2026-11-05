# Modbus service technical reference

For installation and daily use, start with the [service guide](modbus-service.md).
This page describes diagnostics, failure handling and implementation boundaries.
For deployment outside HA OS, see [Docker/Python setup](service-standalone.md).

## Connection and permission model

The service owns the USB or LAN gateway connection, polling, heartbeat and charging intent.
HA connects to its authenticated HTTP API and never opens the adapter in service
mode. This separates the hardware process from HA, as with Z-Wave JS, using HTTP
instead of WebSocket. The shared driver has no Home Assistant dependency; see
[Architecture](architecture.md#independent-service).

The service's read-only flag is authoritative for HA write permission. HA keeps
control entities registered and checks fresh connection/readiness state before
allowing commands. Changing permission notifies HA even when the register values
are unchanged. It sends no HA commands or restored values to the service.
Direct USB and LAN entries retain their separate HA control-mode option.

`connection_type` selects `serial` (the default), `rtu_over_tcp` or `modbus_tcp`.
USB uses `serial_port`, `baudrate` and `frame`. LAN uses `host` and `tcp_port`
(default 502); UART settings belong on the adapter. `device_id` remains the
wallbox address from 10 to 50. The HTTP API's `port` is independent and defaults
to 8765. Neither LAN mode expands the supported RTU register/model scope.

The app's serial-device schema is optional, with no default device path, so a
LAN-only installation needs no `/dev/ttyUSB0`. Existing saved USB settings remain
valid; the runtime validates connection-specific required fields. Optional schema
fields follow [Supervisor's options rules](https://contributors.home-assistant.io/apps/guides/options/).

Service settings are read only at startup: save configuration changes and restart
the service to apply them. This briefly interrupts the heartbeat; HA does not need
restarting for permission changes. The service's own error-200 recovery can still
reassert its saved charging state, including a deliberate pause. The absence of
HA command replay does not disable that recovery.

An unusable optional maximum-current capability is treated as unknown, with the
raw reading retained for diagnosis. It does not prevent setup. Non-finite values
and invalid payload types are rejected. Service and HA current caps still
constrain limited-current commands. On resume, the service applies HA's cap to its
latest saved setpoint while holding the current-write lock; a deliberately
unlimited setpoint stays unlimited.

## Supervisor discovery and distribution

The app generates and stores its private token. Supervisor passes its address
and credential to HA through authenticated local discovery; there is no manual
app-token option or broadcast credential. Discovery is restored after Core
restarts. On confirmation or retry, HA retrieves the current credentials directly
from Supervisor, so a pending flow does not need an app restart to refresh them.
An authentication failure on an existing entry opens the
[access-renewal flow](troubleshooting.md#renew-service-access). It retrieves
fresh credentials for the same app or asks for a new token for a standalone
service, checks the entry's existing identity binding and reloads the entry.
Serial-bound entries require the same serial number; standalone entries added
without a readable serial number retain their URL binding.
When upgrading from beta 1, update the integration first: later apps replace the
old manually configured credential and HA learns the replacement automatically.

The internal API port is 8765. Host publication is disabled by default.
Integration discovery checks the app slug and restricts connections to its
internal hostname and expected port. Standalone services use an explicitly
configured URL and token.

Supervisor downloads the verified image for amd64 or aarch64 and creates the
local app image. The **Building** phase does not compile Python or install
runtime dependencies on the device. Catalogs use immutable image digests;
standalone users can use the same published digest.

Stable releases are published with the same source and image digest to `#apps`
and `#apps-beta`, unless the beta catalog already contains a newer prerelease.
Thus a beta-app installation can receive the stable release as an ordinary
update, preserving its token and state. Prereleases are offered only in `#apps-beta`.
The two catalogs still create different apps in Supervisor. An optional channel
change requires a deliberate one-owner handover, preserving a backup and stopping
the old app before starting the new one.

## Logs and diagnostics

The app log reports process start, accepted bus settings, API readiness, serial
connection, heartbeat start/stop and clean shutdown. Connection and heartbeat
failures are logged on state changes, not on each beat or poll. Read-only mode
explicitly reports that heartbeat delivery is disabled. Lifecycle messages do
not include tokens or wallbox/adapter serial numbers.

Integration diagnostics fetch the app's bounded `write_transactions` history on
demand. It records failed writes and the next confirmation for affected registers,
including time waiting for the bus and send/receive callback timing. Its last 20
records stay in RAM and are cleared by an app restart. There are no packet
contents, credentials, additional Recorder rows or regular diagnostic polls.

Download diagnostics before restarting the app after a fault. Software timing
and callback counts do not identify a faulty cable by themselves; see
[isolated heartbeat timeouts](troubleshooting.md#an-occasional-no-response-received-after-0-retries).

## Startup and worker failures

**Invalid or unreadable service configuration** identifies the startup phase
and invalid option. A missing serial adapter is retried while the API remains
running. An unexpected worker exit is logged and closes the serial connection;
a fatal process failure reports its error class and errno.

The heartbeat worker is supervised independently of polling. If it exits
unexpectedly, the service stops so Supervisor's enabled Watchdog can restart it.
A live API cannot conceal a permanently stopped heartbeat worker. Ordinary
missing acknowledgements use the heartbeat's retry cadence rather than restarting
the whole process. Watchdog checks process liveness, not wallbox readiness;
recurring failures require their logged cause to be corrected.

## Saved state and database traffic

Only pause intent, charging-release intent and the resume current are saved in
`intent.json`, bound to the wallbox identity. The file is atomically replaced with
owner-only permissions when those settings change. Both the file and directory
are synced before a save is considered complete. This is bounded state rather
than a growing history. Include the data directory in backups and protect its
configuration/token.

Heartbeats and polls do not create entries in that state file. The heartbeat
sensor records health transitions, not timestamps for every beat. Measurement
entities keep their existing cadence and can still create Recorder history;
use the [Recorder guide](recorder.md) to control that traffic.

## Commands and API boundary

The service enforces the same current-write floor, phase-switch guards, device
limits and bounded error-200 recovery as direct mode. All clients share one
serialized command path. Failed or timed-out commands are not automatically
replayed by HA because a write may already have reached the wallbox. Persistent
recovery failures remain visible. Recovery while deliberately paused still needs
confirmation on the actual wallbox firmware.

At most eight API bus requests may be in flight. Excess requests fail promptly
instead of creating an unbounded queue. Heartbeats retain priority over bus reads
and commands. Normal state requests read the cache without adding Modbus traffic.

The API exposes named actions and an allowlist of diagnostic reads, with bearer
authentication and version checks. It has no arbitrary register-write or
remote-shutdown endpoint. `/health` exposes process liveness only. An unreachable
service or wallbox makes HA readings unavailable; reconnecting does not replay
HA restore values.

The service uses plain HTTP. Use a trusted private network or an HTTPS reverse
proxy outside that network. The client does not follow redirects and does not
accept credentials embedded in URLs. See the [security model](../SECURITY.md#threat-model)
and [privacy policy](../PRIVACY.md).

## Build a local development checkout

To test source changes without using the published image:

```bash
python scripts/build_service_app.py --output /tmp/mennekes-amtron-app
```

Copy this directory to HA's local app directory and install the local app.
The export includes source and a development Dockerfile so Supervisor builds
the checkout. Alternatively, from the repository root:

```bash
docker build -t amtron-modbus-service:local .
```

Local apps and published-catalog apps are different installations. Stop the
current serial owner before switching. Automated HA, virtual serial and HTTP
tests exercise the process boundary; they do not replace physical wallbox and
Supervisor acceptance tests.
