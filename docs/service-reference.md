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

For standalone configuration, `connection_type` selects `serial` (the default),
`rtu_over_tcp` or `modbus_tcp`. App 0.5.2b2 offers exactly **USB / RS-485** (default),
**LAN: Modbus RTU (TCP)** and **LAN: Modbus TCP**. The service normalizes these
names to the same three protocols. Standalone JSON keeps accepting the technical
names. Saved app choices using those names need a one-time
[selection of the matching readable choice](upgrading.md#app-connection-choice);
no automatic connection change is performed.
USB uses `serial_port`, `baudrate` and `frame`. LAN uses `host` and `tcp_port`
(default 502); UART settings belong on the adapter. `device_id` remains the
wallbox address from 10 to 50. The HTTP API's `port` is independent and defaults
to 8765. Neither LAN mode expands the supported RTU register/model scope.

In published apps **0.5.1–0.5.2b2**, the native Configuration tab has an optional
serial-device selector with no default device path. It keeps the host field
visible, accepts an empty host for USB and does not hide fields when changing
protocol. A stale saved USB device can prevent Supervisor from saving LAN
settings; clear that serial selection first. A LAN-only installation needs no
local serial device.

**Next beta, unreleased:** `serial_port` becomes an optional string in the native
schema. A separate **Open Web UI** page supplies a USB dropdown and only the
fields relevant to the selected transport. It prefers stable adapter paths and
removes the serial path when saving LAN. USB runtime validation still restricts
paths to supported serial devices; the schema change grants no additional device
access. See [connection setup](upgrading.md#app-connection-page).

The new page uses a separate internal Ingress listener on port 8099 and requires
an administrator with stable Supervisor **2026.11.0+**. It accepts requests only
from Supervisor, validates administrator identity and protects writes against
cross-site requests and concurrent option changes. It reads only locally mapped
serial devices, saves through Supervisor's own-app API and never sends the
service token to the browser. There is no host-published settings port or need
for additional Supervisor roles. Native Configuration remains available for
read-only mode, current limits, intervals and manual configuration.

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

### State-change interval

`state_change_interval_seconds` accepts whole seconds from 0 to 3600 and defaults
to 300. The app and standalone service use the same setting; HA's service-mode
options do not impose another timer. Zero disables the long transition guard,
while the five-second current-write floor remains. Ordinary current adjustment
during charging uses that short floor. Pause/resume, phase changes, both
charging-release directions and lock/unlock use the shared state-change interval.

The service keeps one pending charging request. A newer request supersedes it;
pause, release-off and lock-on replace pending starts and also wait for the
long interval when necessary. A pause that hits the five-second current-write
floor is queued too; the caller does not need to repeat it. Repeating the
same state does not extend the deadline. Recovery
that reasserts existing intent and the independent heartbeat do not wait for
this interval. A compound pause-before-phase command waits for live confirmation
of the paused state for up to 30 seconds before sending the phase command.
With a connected vehicle, layout v01.02 and newer require EVSE state 2 and CP
state B1; layout v01.01 uses EVSE state 2 because CP state is unavailable.
`phase_switch_grace_seconds` adds a separate wait after confirmation: whole
seconds from 0 to 30, default 5. Status is rechecked after that wait. Zero removes
only the extra grace time; confirmation and the state-change interval remain.
A failed confirmation or timeout leaves the phase command unsent and retains
the pause. A successful phase change still leaves charging paused; it does not
automatically resume. Confirmation and grace waits run in the background queue,
so an HTTP command does not wait for the full sequence. During confirmation,
the phase action remains pending with a `null` due timestamp; after confirmation,
the timestamp gives the end of the grace period. An idle wallbox with no vehicle
does not need pause confirmation or grace time. Fresh EVSE Idle and CP A1/A2
readings also skip the state-change interval. Missing CP support, unreadable or
inconsistent readings retain the interval. Pending waits check for unplugging
every five seconds without updating the due timestamp. Register-write guards
and heartbeat remain active.

The queue belongs to the running service, so it can execute while HA is offline.
Restarting the service cancels pending work and begins a conservative guard
period. Neither HA reconnect nor saved intent replays a cancelled queued start.
See [Waiting between charging changes](user-guide.md#waiting-between-charging-changes).

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
Integration and service releases are independent. The current integration
accepts **service 0.5.0 or newer with API 2**, including the already published
0.5.0 app. A routine integration update leaves that service running. If both
need updating, update the app/service first, then the integration and restart
Core. Keep existing app data and the HA entry; see [Updates](modbus-service.md#updates).

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

The app log reports process start, accepted bus settings, API readiness, USB/LAN
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
running. An unexpected worker exit is logged and closes the Modbus connection;
a fatal process failure reports its error class and errno.

In the **next beta, unreleased**, an app missing the selected transport's serial
path or LAN host stays in setup mode after validating all other options. Its
health endpoint reports `configured: false`; Modbus, heartbeat and discovery do
not start. Save the connection in **Open Web UI** or native Configuration, then
restart the app. A configured but temporarily absent adapter still uses normal
retries. Invalid values and standalone configuration errors remain fatal rather
than being silently converted into setup mode.

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

In version 0.5.2b2, the pending action and its UTC due time, when known, are kept in
memory. These fields change on scheduling, replacement or completion, not on
every second of the wait. The frontend can display the due time relatively;
the database does not need a changing countdown value. Pending work is not
added to `intent.json` and is cancelled on service restart.

## Commands and API boundary

Service **0.5.0 introduced API 2**. Snapshots report `api_version: 2`, and
authenticated endpoints use `/v2/`. The beta API 1 and `/v1/` routes are
intentionally unsupported; there are no compatibility aliases.

The integration checks the supported API and its separate minimum service
version, currently **0.5.0**. Release numbers do not have to match: compatible
newer service versions are accepted. New snapshots report `service_version`.
The published 0.5.0 app predates that field and is accepted as the known API 2
baseline; an omitted version will no longer satisfy a future higher minimum.
An unsupported API or a service below the minimum produces the compatibility
error. A malformed version or snapshot produces the data-validation error.
Neither condition asks for new credentials. See [Updates](modbus-service.md#updates).

Version 0.5.2b2 adds these fields to the snapshot's `control` object:

| Field | Meaning |
|---|---|
| `state_change_interval_seconds` | The service's configured interval, default 300 seconds |
| `phase_switch_grace_seconds` | Extra wait after confirmed pause before switching phases, default 5 seconds |
| `pending_action` | The queued action, or `null` when nothing is pending |
| `pending_until` | Fixed timezone-aware UTC timestamp for a timed wait; `null` with no pending action or while a phase change awaits vehicle pause confirmation |

No pending action means no due timestamp. An ordinary pending action has a due
timestamp; `single_phase` and `all_phases` may instead have `null` while waiting
for vehicle pause confirmation. Once confirmed, a fixed timestamp identifies
the end of the grace period. These additions are optional for older API 2
services. Their absence does not enable the new queue in that service; its
existing control behavior remains. HA derives the two pending-change sensors
from the service data rather than maintaining a second queue.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Public process-liveness check; no wallbox data. |
| `GET /v2/state` | Authenticated cached wallbox state and service status. |
| `GET /v2/diagnostics` | Authenticated bounded diagnostic history, without bus reads. |
| `POST /v2/read` | Authenticated diagnostic read from the register allowlist. |
| `POST /v2/command` | Authenticated named control command with an identity precondition. |

Every command must include `expected_serial_number`, using the serial number
already bound to the client's configured wallbox. For example, a pause request
to `/v2/command` has this body; replace the example serial with that configured
identity:

```json
{
  "command": "pause",
  "expected_serial_number": "EXAMPLE_CONFIGURED_SERIAL"
}
```

The service compares this precondition with its initialized wallbox identity
while holding the command lock, before writing. A missing field or mismatched
serial is rejected. Explicit `null` is accepted only when the service also has
no readable serial number; it is **not a wildcard**. Such endpoint-bound setups
cannot independently verify the physical wallbox. Clients with a known serial
number must retain that binding instead of accepting a newly observed device.

The released 0.5.1 service compares the identity read during initialization.
The 0.5.2b2 driver additionally verifies a known serial over Modbus after a
reconnection and before control writes. This also detects a changed wallbox
behind a gateway whose TCP connection stayed open. A missing or different
serial blocks the write without changing saved intent; the service reports
unavailable until the original device can be verified again. Direct mode uses
the same driver checks. Normal heartbeats reuse the verified connection;
reconnects and a detected identity mismatch require verification again.

Stop the bus owner before deliberately changing a gateway route or connected
wallbox. Modbus serial numbers are not cryptographic authentication, and a
physical routing change is not an authenticated identity change notification.

The service enforces the same current-write floor, charging transition guards,
device limits and bounded error-200 recovery as direct mode. All clients share one
serialized command path. A command is rejected if the latest wallbox snapshot
is unavailable or older than the greater of 30 seconds and three status-poll
intervals. This is checked after waiting for the command lock; a queued request
cannot use readiness that expired while it waited. Rejected commands are not
replayed later, and this check does not stop the heartbeat or change saved pause
intent. In version 0.5.2b2, an accepted command can instead be queued for its
state-change deadline; execution rechecks its prerequisites, and an execution
failure clears pending work rather than retrying an uncertain write indefinitely.
Failed or timed-out commands are not automatically
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
