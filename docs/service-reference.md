# Modbus service technical reference

[Documentation index](README.md) · [Architecture](architecture.md) · [Validation](validation.md)

For maintainers and technical investigations; normal setup starts with
[Installation](installation.md).

For installation and daily use, start with the [service guide](modbus-service.md).
This page describes diagnostics, failure handling and implementation boundaries.
For deployment outside HA OS, see [Docker/Python setup](service-standalone.md).

[Connection](#connection-and-permission-model) · [Diagnostics](#logs-and-diagnostics) · [State](#saved-state-and-database-traffic) · [API](#commands-and-api-boundary) · [Build locally](#build-a-local-development-checkout)

## Connection and permission model

The service owns the USB or LAN gateway connection, polling, heartbeat and charging intent.
HA connects to its authenticated HTTP API and never opens the adapter in service
mode. This separates the hardware process from HA, as with Z-Wave JS, using HTTP
instead of WebSocket. The shared driver has no Home Assistant dependency; see
[Architecture](architecture.md#one-service-runtime).

The service's read-only flag is authoritative for HA write permission. HA keeps
control entities registered and checks fresh connection/readiness state before
allowing commands. Changing permission notifies HA even when the register values
are unchanged. It sends no HA commands or restored values to the service.
Direct USB and LAN entries retain their separate HA control-mode option.

For standalone configuration, `connection_type` selects `serial` (the default),
`rtu_over_tcp` or `modbus_tcp`. The app offers the same three protocols as
**USB / RS-485** (default), **LAN: Modbus RTU (TCP)** and **LAN: Modbus TCP**.
USB uses `serial_port`, `baudrate` and `frame`. LAN uses `host` and `tcp_port`
(default 502); UART settings belong on the adapter. `device_id` remains the
wallbox address from 10 to 50. The HTTP API's `port` is independent and defaults
to 8765. Neither LAN mode expands the supported RTU register/model scope.

For compatible Waveshare adapters, `tcp_authentication: true` enables a fresh
password handshake on every TCP connection, using `tcp_auth_password`. This is
the existing adapter web password, separate from the service API token. Login
failure closes the socket; Modbus traffic is not sent through an unverified
connection. Passwords are not included in diagnostics or configuration reprs.
The app and HA setup clear the saved adapter password when disabling login or
switching to USB. For standalone JSON, remove `tcp_auth_password` yourself when
turning login off.
See [TCP login limits](lan-adapters.md#tcp-login-and-its-limits): this protocol
provides no encryption, and in this project's own test firmware 1.523 accepted
a login without the password.

Compatible adapter setup is a separate explicit operation in the HA flow or
app connection page. It pins the inspected address and native identifier,
checks the supported firmware/layout and sets a passive TCP-server profile
without storage queries, flow control, preambles or serial replay. It preserves
unrelated parameters and reads back the whole change. Unknown payload/flag
formats are refused before writing. No parameter writes occur during ordinary startup or reconnect.
The app closes and suspends its old Modbus connection before an explicit apply;
a successful save requires an app restart, while an uncertain restore keeps the
old connection blocked. See [LAN adapter setup](lan-adapters.md).

If a Supervisor options write cannot be confirmed, the running app rejects
later connection saves, including ordinary saves, to avoid racing the delayed
write. Reading settings and detecting an adapter remain available. This
process-local block does not cancel the earlier Supervisor request or prevent
changes made elsewhere; an app restart alone does not prove that the request has
finished. Check the saved options and adapter before reconnecting.

Before saving, the connection page checks that the Supervisor options still
match those it read. A detected concurrent edit is rejected; reload the page
before trying again. Supervisor has no atomic compare-and-save API, so an edit
made elsewhere between the final check and the save can still race. Use one
configuration page at a time, including the native app Configuration tab.

`serial_port` is an optional string in the native schema. The **Connection**
page under **Open Web UI** supplies a USB dropdown and only the fields relevant
to the selected transport. It prefers stable adapter paths and removes the
serial path when saving LAN. USB runtime validation restricts paths to
supported serial devices. A LAN-only installation needs no local serial device.

The web pages use a separate internal Ingress listener on port 8099 and need
Supervisor **2026.10.4+**. They accept requests only from Supervisor, require a
Home Assistant administrator and protect writes with a CSRF token, a revision
check and a strict content security policy. They read only locally mapped serial
devices, save through Supervisor's own-app API and never send the service token
to the browser. There is no host-published settings port or need for additional
Supervisor roles. See [the app's web pages](app-guide.md).

| Ingress route | Purpose |
|---|---|
| `/`, `GET /api/overview` | Overview page and its read-only snapshot |
| `/connection`, `/api/config`, `/api/adapter` | Connection settings and LAN-adapter setup |
| `/log`, `/api/modbus-debug` | Live log, in RAM only |
| `/registers`, `GET /api/registers` | Register page below the overview, from passive observations |
| `/runtime`, `/api/runtime`, `POST /api/runtime/preview` | Runtime settings with preview |
| `/targets`, `/api/charging` | Targets and explicit guest start |

Values saved on the **Runtime** page apply without a restart. Each previewed
change names its `effect`: `next_cycle` for the polling intervals, `immediate`
for the current cap and the state-change interval, `next_switch` for the
phase-switch grace. After every poll the service lowers a charging current
above its cap, unless a queued command, a phase switch, a reached goal prevents the change. `restart_fields` lists the
connection, read-only mode, listen address and port when their saved value
differs from the running one.
All other service settings are read only at startup: save configuration changes
and restart the service to apply them. This briefly interrupts the heartbeat; HA does not need
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
Update the app/service first, then the integration, and restart Core. Keep the
app data and the HA entry; see [Updates](modbus-service.md#updates).

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

An app missing the selected transport's serial
path or LAN host stays in setup mode after validating all other options. Its
health endpoint reports `configured: false`; Modbus, heartbeat and discovery do
not start. Save the connection on the **Connection** page or in native Configuration, then
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

The service keeps three files, each bound to the wallbox identity:

| File | Content | Written |
|---|---|---|
| `intent.json` | Charging intent, shared energy and percentage limits, bounded evidence for restoring guest permission | When that intent changes |
| `departure-plan.json` | The selected time, the vehicle plan and its bounded learned model | When the plan changes |
| `charge-meter.json` | The meter log of the running and the last plug cycle, the plug cycle and its note | At a change of the plug cycle, and every five minutes while the meter rises |

An intent file of another format is rejected and requires a safe reset
acknowledgement. An unusable `charge-meter.json` only starts measuring again.
There is no guest-order file, separate guest energy baseline or charging-rate
history. Guest permission is kept only in service memory for the current plug-in.

State updates use atomic replacement and owner-only permissions. Include the
service data directory and private token in protected backups. A rejected
intent file never counts as charging permission; the acknowledgement is
described in [Updates](installation.md#updates).

Heartbeats and polls do not write `intent.json` or `departure-plan.json`; the
charge meter is the only file saved on a timer, and only while a car charges. The heartbeat
sensor records health transitions, not timestamps for every beat. Measurement
entities keep their existing cadence and can still create Recorder history;
use the [Recorder guide](recorder.md) to control that traffic.

The pending action and its UTC due time, when known, are kept in
memory. These fields change on scheduling, replacement or completion, not on
every second of the wait. The frontend can display the due time relatively;
the database does not need a changing countdown value. Pending work is not
added to `intent.json` and is cancelled on service restart.

## Commands and API boundary

Snapshots report `api_version: 3`, and authenticated endpoints use `/v2/`.
The integration checks the API version and the snapshot's `service_version`.
An unsupported API or a too old service produces the compatibility error; a
malformed version or snapshot produces the data-validation error. Neither
condition asks for new credentials. See [Updates](modbus-service.md#updates).

The snapshot's `control` object includes:

| Field | Meaning |
|---|---|
| `state_change_interval_seconds` | The service's configured interval, default 300 seconds |
| `phase_switch_grace_seconds` | Extra wait after confirmed pause before switching phases, default 5 seconds |
| `pending_action` | The queued action, or `null` when nothing is pending |
| `pending_until` | Fixed timezone-aware UTC timestamp for a timed wait; `null` with no pending action or while a phase change awaits vehicle pause confirmation |

No pending action means no due timestamp. An ordinary pending action has a due
timestamp; `single_phase` and `all_phases` may instead have `null` while waiting
for vehicle pause confirmation. Once confirmed, a fixed timestamp identifies
the end of the grace period. HA derives the two pending-change sensors from the
service data rather than maintaining a second queue.

The snapshot's `charge_meter` object describes what the service measured for
cost and loss estimates; times are epoch seconds:

| Field | Meaning |
|---|---|
| `known_since` | Since when the plug state has been known without a long gap |
| `present` | Whether a car is plugged in; `null` while unknown |
| `plugged_at`, `charging_at` | Plug-in and first charging of the running cycle; `null` unless the service saw the plug-in |
| `note` | The note Home Assistant attached to the running cycle with `charge_note` |
| `last`, `last_note` | Plug-in and unplug of the last cycle seen from end to end, and its note |

| Endpoint | Purpose |
|---|---|
| `GET /health` | Public process-liveness check; no wallbox data. |
| `GET /v2/state` | Authenticated cached wallbox state and service status. While a command runs, the request waits up to two seconds and then returns the state published after the last completed command or poll, if that is at most a minute old. |
| `GET /v2/diagnostics` | Authenticated bounded diagnostic history, without bus reads. |
| `POST /v2/read` | Authenticated diagnostic read from the register allowlist. |
| `POST /v2/energy` | Authenticated: the energy the meter delivered between `start` and `end` (epoch seconds), from the service's bounded meter log; `null` when the log does not cover the span. No bus access. |
| `POST /v2/command` | Authenticated named control command with an identity precondition. |
| `POST /v2/soc-goal/reserve`, `/commit`, `/resolve` | Authenticated percentage-goal transaction: reserve a token, commit the goal, resolve an uncertain outcome. |

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

Each command takes only its own fields; any other field is refused as an
invalid request, also one that another command would take. Commands that only
carry plan data or a rule's setting, and `cancel_pending`, are accepted while
the wallbox does not answer; every other command needs it.

The recovery command `acknowledge_intent_reset` needs only `command` and
`expected_serial_number`, apart from the optional activity `origin`. It
acknowledges a rejected saved control file after the user reviews the reset.
Normal commands remain blocked while
`control.intent_rejected` is true. The acknowledgement durably saves paused,
release-off, 6 A defaults before lifting that block; it sends no start command.
If saving fails, the block remains; an atomic replacement that already completed
may have made the safe defaults visible on disk. HA exposes this through
**Settings → System → Repairs**. Repeated acknowledgements after a successful
reset leave the current intent unchanged.

### Command activity metadata

Command requests may carry `origin`. It is an allowlisted diagnostic
source, not an authenticated user identity. The service validates and removes it
before dispatching the command. It is a common optional field in addition to
the action-specific fields documented here. A queued command retains its origin
even if a later request or background task executes it.

Each command event includes `source`, `occurred_at` (timezone-aware UTC), and
`requested_value` (nullable, not physical current readback), as well as the existing
sequence/action/outcome/reason fields. The bounded feed retains 32 terminal
outcomes. P&C gate writes appear only when a release is actually written; polls
and heartbeat writes do not enter this feed. Consumers must distinguish command
acknowledgement from the independently observed charging state.

### Departure plan

The service plans a departure itself; see
[departure planning](departure-planning.md#how-the-service-plans). The
snapshot's `control` object reports `departure`, which is `null` while the
saved plan loads:

| Field | Meaning |
|---|---|
| `trip` | `deadline` (ISO timestamp or `null`), `source` and `finished` (`reached`, `late` or `null`) |
| `edit_revision` | Opaque persisted identifier changed by explicit trip edits, not by automatic completion; HA sends it with a time taken from a calendar or the car, so an edit made meanwhile is not overwritten |
| `editor`, `selection_vehicle` | Last explicit editor (`ha` or `app`) and its vehicle binding; not public vehicle identity labels |
| `status` | The plan's state, as shown by the `departure_plan` attribute of **Charging wait reason** |
| `planned_start` | When charging starts or started for this trip, or `null` |
| `estimated_soc` | The projected charge level in percent, or `null` |
| `inputs` | Digest of the car data last handed over, or `null` |
| `scope` | Opaque digest of the current plug-in, or `null` without a car |
| `confirmed` | Whether the handed-over car is confirmed for this plug-in |
| `forecast` | `null` unless a plan is computed; otherwise `finish_at` (when the target is reached: the departure, or later when it is unreachable, rounded up to five minutes), `level_at_departure` (percent), `efficiency` (learned share of the metered energy that reaches the battery, 0.9 until learned) and `full_power_kw` (metered power at full current for the current phases, or `null`) |

`control.guest_active` reports guest charging until a requested pause is confirmed. `control.guest_mode` also remains true while that guest is paused,
so the vehicle planner cannot take over. `control.guest_revision` binds an explicit
start to the current choices and physical connection.
`control.guest_confirmation_required` means restart evidence is missing or
inconsistent: charging stops until a fresh guest start or confirmed unplug.
There is no `control.orders`.

`control.operation` summarizes all controllers of the service for a reader. It
is derived from their published state and decides nothing:

| Field | Meaning |
|---|---|
| `controller` | `unavailable`, `guest`, `departure_plan` or `direct`. Guest permission takes priority over a vehicle departure plan |
| `waiting` | The first reason that keeps the car from charging, or `none`: `wallbox_unavailable`, `no_vehicle`, `release_off`, `plug_charge_waiting`, then the controller's reason (`meter_unavailable`, `vehicle_unconfirmed`, `until_planned_start`, `estimated_target_reached`, `waiting_for_device`, `waiting_for_soc`, `waiting_for_capacity`, `waiting_for_target`, `read_only`, `command_pending`), then `target_reached`, `command_pending` or `paused` |
| `next` | `event` (`none`, `queued_command`, `plan_start`, `target_reached`, `departure`) and `at`, an ISO timestamp that is `null` exactly for `none`. A queued command is named when it comes first |
| `target` | `kind` (`none`, `level` in percent, `energy` in kWh), `value` and `deadline` (ISO timestamp or `null`) |
| `vehicle` | `unknown`, `none`, `connected`, or `confirmed` once Home Assistant confirmed its car for this plug-in |

The overview page receives the same object as `operation` from
`/api/overview`, or `null` when it does not validate.

The service keeps the one trip; Home Assistant holds no copy of it. Three
commands carry data from Home Assistant without accessing the bus: one sets
the trip, one hands over the car's data, one names the car of the plug cycle. Each needs
`expected_serial_number`, rejects other fields and is accepted while the
wallbox is unreachable:

| Command | Additional fields | Behavior |
|---|---|---|
| `departure_trip` | `deadline`: timezone-aware ISO timestamp or `null`; `source`: string up to 512 characters or `null`; optional `withdraw`: boolean; optional `expected_selection`: `{"edit_revision": …}` as last reported | Sets or clears the trip. With `expected_selection`, a trip edited meanwhile refuses the command with `soc_goal_source_changed`. The same time and source leave a running plan unchanged. `withdraw` also forgets the car and ends a pause the plan set. |
| `departure_inputs` | `value`: object with `vehicle` (the configured charge-level source), `scope` (the plug-in the car is confirmed for, or `null`), `capacity_kwh`, `phases`, `soc_percent`, `soc_observed_at` and `minimum_percent` (0 to 100; below it the plan charges at once at full current with status `minimum_level`, 0 switches that off) | Replaces the car data. A charge level needs its report time; a report older than the one in use is ignored. |
| `charge_note` | `plugged_at`: the `charge_meter.plugged_at` of the running plug cycle; `note`: string of 1 to 128 characters or `null` | Attach a note to the running plug cycle, such as the car Home Assistant recognized. The note means nothing to the service; it stays with the cycle as `charge_meter.note` and, after the unplug, as `charge_meter.last_note`. A cycle that is over keeps what it had. No bus access. |

The plan writes through the same guarded path as other commands, with the
owner `departure`. It is saved in `/data/departure-plan.json`, together with
what the plan learned about up to eight cars, at most 8 KiB and bound to the
wallbox identity. An unreadable file only clears the plan, and what it learned,
until Home Assistant hands it over again. HA must be running to start climate
actions.

The service compares this precondition with its initialized wallbox identity
while holding the command lock, before writing. A missing field or mismatched
serial is rejected. Explicit `null` is accepted only when the service also has
no readable serial number; it is **not a wildcard**. Such endpoint-bound setups
cannot independently verify the physical wallbox. Clients with a known serial
number must retain that binding instead of accepting a newly observed device.

The driver also verifies a known serial over Modbus after a reconnection and
before control writes. This also detects a changed wallbox
behind a gateway whose TCP connection stayed open. A missing or different
serial blocks the write without changing saved intent; the service reports
unavailable until the original device can be verified again. A direct entry
runs this same service inside Home Assistant. Normal heartbeats reuse the verified connection;
reconnects and a detected identity mismatch require verification again.

Stop the bus owner before deliberately changing a gateway route or connected
wallbox. Modbus serial numbers are not cryptographic authentication, and a
physical routing change is not an authenticated identity change notification.

The service enforces the current-write floor, charging transition guards,
device limits and bounded error-200 recovery for every client. All clients share one
serialized command path. A command is rejected if the latest wallbox snapshot
is unavailable or older than the greater of 30 seconds and three status-poll
intervals. This is checked after waiting for the command lock; a queued request
cannot use readiness that expired while it waited. Rejected commands are not
replayed later, and this check does not stop the heartbeat or change saved pause
intent. An accepted command can instead be queued for its
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

## Guest charging API

Read `control.guest_active`, `control.guest_mode` and `control.guest_revision` in `GET /v2/state`.
Start with `POST /v2/command` naming `command: guest_start`,
`expected_serial_number` and `expected_guest_revision`. A stale revision or a
different wallbox is rejected. There is no `/v2/jobs` or `/api/jobs` route.

The command uses the existing energy baseline and selected time. It grants release
for this connection and lifts manual pause, while preserving locks, faults and
current limits. It does not clear the saved vehicle percentage target; that target
is ignored during the guest session. Normal pause stops charging; the vehicle planner stays excluded until unplugging.

Admin-only `/api/charging` exposes the shared energy, percentage and time choices
and the explicit guest action. Writes carry revisions and existing CSRF protection;
failed starts are not retried. Vehicle profile and intelligent planning configuration
remain in HA. The percentage editor uses the bound source and optional maximum.

## Transport security

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
