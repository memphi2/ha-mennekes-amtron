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
provides no encryption and firmware 1.523 has a confirmed handshake weakness.

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
| `/api/modbus-debug` | Debug log, in RAM only |
| `/registers`, `GET /api/registers` | Register explorer, from passive observations |
| `/runtime`, `/api/runtime`, `POST /api/runtime/preview` | Runtime settings with preview |
| `/jobs`, `/guest`, `/api/jobs` | Charging jobs, boost and guest charging |

Values saved on the **Runtime** page apply without a restart. Each previewed
change names its `effect`: `next_cycle` for the polling intervals, `immediate`
for the current cap and the state-change interval, `next_switch` for the
phase-switch grace. After every poll the service lowers a charging current
above its cap, unless a queued command, a phase switch, a goal or order stop or
a holding charging job decides the current. `restart_fields` lists the
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

Pause intent, charging-release intent and the resume current are saved in
`intent.json`, bound to the wallbox identity. A charging job is saved in
`charge-job.json`, weekly plans in `weekly-plans.json`, learned charging power
in `charge-rates.json` and the departure plan in `departure-plan.json` the same
way. The file is atomically replaced with
owner-only permissions when those settings change. Both the file and directory
are synced before a save is considered complete. This is bounded state rather
than a growing history. Include the data directory in backups and protect its
configuration/token.

Heartbeats and polls do not create entries in that state file. The heartbeat
sensor records health transitions, not timestamps for every beat. Measurement
entities keep their existing cadence and can still create Recorder history;
use the [Recorder guide](recorder.md) to control that traffic.

The pending action and its UTC due time, when known, are kept in
memory. These fields change on scheduling, replacement or completion, not on
every second of the wait. The frontend can display the due time relatively;
the database does not need a changing countdown value. Pending work is not
added to `intent.json` and is cancelled on service restart.

## Commands and API boundary

Snapshots report `api_version: 2`, and authenticated endpoints use `/v2/`.
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

| Endpoint | Purpose |
|---|---|
| `GET /health` | Public process-liveness check; no wallbox data. |
| `GET /v2/state` | Authenticated cached wallbox state and service status. |
| `GET /v2/diagnostics` | Authenticated bounded diagnostic history, without bus reads. |
| `POST /v2/read` | Authenticated diagnostic read from the register allowlist. |
| `POST /v2/command` | Authenticated named control command with an identity precondition. |
| `GET`/`POST /v2/jobs` | Authenticated charging-job state and operations; see [Charging job API](#charging-job-api). |

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

Timed plans use two additional commands:

| Command | Additional fields | Behavior |
|---|---|---|
| `timed_current` | `value`: requested amperes ≥6 (limited to the configured maximum), or `null` for pause; `owner`: nonempty string up to 128 characters; `deadline`: timezone-aware ISO timestamp up to 64 characters | Applies or queues this plan's requested current/pause only before its deadline. |
| `cancel_timed_current` | `owner`: the same owner string | Removes only an unsent queued command belonging to that plan; manual queued work is preserved. |

Both require `expected_serial_number` as above. The deadline is checked inside
the service, so an obsolete queued request expires even while HA is offline.
Expiry and cancellation do not reverse a command already applied or alter the
last charging state. The integration cancels its owned pending work when a plan
is cleared, replaced, completed or unloaded. Pending work remains volatile and
is not restored after a service restart. The integration uses these two
commands only with a service that does not plan departures itself.

### Departure plan

The service plans a departure itself; see
[departure planning](departure-planning.md#with-the-modbus-service-app). The
snapshot's `control` object reports `supports_departure_plan: true` and
`departure`, which is `null` while the saved plan loads:

| Field | Meaning |
|---|---|
| `trip` | `deadline` (ISO timestamp or `null`), `source` and `finished` (`reached`, `late` or `null`) |
| `status` | The plan's state, as shown by **Timed charging status** |
| `planned_start` | When charging starts or started for this trip, or `null` |
| `estimated_soc` | The projected charge level in percent, or `null` |
| `inputs` | Digest of the car data last handed over, or `null` |
| `scope` | Opaque digest of the current plug-in, or `null` without a car |
| `confirmed` | Whether the handed-over car is confirmed for this plug-in |

Two commands hand the plan over. Both need `expected_serial_number`, reject
other fields and are accepted while the wallbox is unreachable, because they
access no bus:

| Command | Additional fields | Behavior |
|---|---|---|
| `departure_trip` | `deadline`: timezone-aware ISO timestamp or `null`; `source`: string up to 512 characters or `null`; optional `withdraw`: boolean | Sets or clears the trip. The same time and source leave a running plan unchanged. `withdraw` also forgets the car and ends a pause the plan set. |
| `departure_inputs` | `value`: object with `vehicle` (the configured charge-level source), `scope` (the plug-in the car is confirmed for, or `null`), `capacity_kwh`, `phases`, `soc_percent`, `soc_observed_at` and `minimum_percent` (0 to 100; below it the plan charges at once at full current with status `minimum_level`, 0 switches that off) | Replaces the car data. A charge level needs its report time; a report older than the one in use is ignored. |

The plan writes through the same guarded path as `timed_current`, with the
owner `departure`. It is saved in `/data/departure-plan.json`, at most 8 KiB
and bound to the wallbox identity. An unreadable file only clears the plan
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

## Charging job API

The [charging job pages](app-guide.md#charging-jobs) use the same operations as
the authenticated `/v2/jobs` endpoint. Ingress uses `/api/jobs` with the
administrator check and the CSRF token from its `GET` response. Both accept at
most 4 KiB, reject query strings and extra fields, and never retry by themselves.

`GET` returns the current order, a `revision` and the connection `scope`. The
scope is an opaque token for the current physical connection, not a vehicle
identifier; it changes when the car is unplugged, the wallbox is unreadable for
more than a minute or the service restarts. Every
`POST` names an `action`:

| Action | Fields besides `action` | Effect |
|---|---|---|
| `preview` | `revision`, `scope`, `request` | Validates an order and returns its canonical form. Writes nothing. |
| `create` | `revision`, `scope`, `request` | Saves the previewed order. Repeating the same request returns its result without resetting it. |
| `preview_guest` | `revision`, `scope`, `request` | Turns `id`, `duration_minutes`, `energy_kwh` and `current_a` into an absolute guest order to confirm with `create`. |
| `preview_boost` | `revision`, `scope`, `minutes` | Returns the absolute `until` for an early start of 1 to 120 minutes. |
| `boost` | `revision`, `scope`, `until` | Starts the previewed boost. |
| `cancel_boost`, `stop`, `clear` | `revision`, `confirm: true` | Ends the boost, stops the order, or removes a stopped or finished order without resuming. |

An order `request` has this form:

```json
{"id": "32 lowercase hexadecimal characters", "start_at": "2026-11-13T22:00:00+01:00",
 "finish_at": "2026-11-14T06:40:00+01:00", "current_a": 12, "energy_kwh": 10}
```

An optional `reserve_kwh` (0.1 kWh steps, at most `energy_kwh`) charges that
part at once and then waits for `start_at`; see
[immediate reserve](app-guide.md#immediate-reserve). `GET` then also reports
`reserve_reached`.

`GET` also returns a `forecast` for the [finish estimate](app-guide.md#finish-estimate),
computed from the meter readings the service polls anyway; it adds no Modbus
reads and changes nothing. `state` is `estimate` when `earliest_finish` and
`latest_finish` hold a range; otherwise it names why there is none, for
example `learning`, `scheduled`, `waiting_vehicle`, `awaiting_meter`, `stale`,
`unstable`, `beyond_horizon`, `unavailable`, `complete` or `stopped`. `source`
is `live` or `history`, `power_kw` the median measured power, `samples` the
number of intervals and `deadline_status` one of `likely`, `at_risk`,
`unlikely` and `unknown`. `history_storage` is `unavailable` while learned
values cannot be saved.

Learned powers are kept per hashed vehicle reference, current and phase count in
`/data/charge-rates.json`: at most 64 of them with 16 values each, for 30 days,
at most 64 KiB, bound to the wallbox identity like the other files. The file
holds no vehicle identifier and no charging permission. It is written at most
every ten minutes and at shutdown; an unreadable or unwritable file only stops
the history, never charging.

Errors return HTTP 409 with a key such as `job_changed` (stale revision or
scope), `job_exists`, `job_not_permitted`, `job_preview_expired`,
`job_current_exceeds_cap`, `guest_vehicle_target_active`,
`job_clear_requires_pause` or `job_storage_unavailable`; malformed input returns
400 with `invalid_job_request`. `job.state` is the order's intent, not a measured
vehicle state: `scheduled`, `charging`, `waiting_meter`, `suspended`, or one of
the final states `reached`, `finished`, `stopped` and `interrupted`; `job.reason`
names why.

While an order holds the connected car (`job.holding` is `true`),
`/v2/command` refuses commands that set the current, resume, switch phases or
start another planner with `service_job_active`. `pause`, `cancel_pending`,
release off and lock on stop the order first. When an order ends, the service
pauses charging. Once the wallbox confirms the pause and the finished order is
saved, `holding` turns `false` and commands work again; the finished order stays
in `GET` until it is cleared or replaced by `create`. The order is saved in
`/data/charge-job.json` before it may start charging.

A service restart or a lasting connection loss turns a running order into
`suspended` (reason `service_restarted` or `connection_lost`). It keeps holding
the car paused and continues on the new `scope` only when the wallbox's session
duration kept pace with the clock within two minutes, its session counter and
the selected vehicle are unchanged and its session energy did not start over.
Otherwise, or without such proof within a minute of a readable wallbox, it
becomes `interrupted` with the same reason. For this proof the saved order
also holds the last session duration, session energy, session counter and a
digest of the vehicle selection; that record is saved only with the order's own
changes.

## Weekly plan API

The service also accepts `GET` and `POST` at `/v2/weekly`; Ingress uses
`/api/weekly` with the same administrator check, CSRF token, size limit and
no-retry rule as the job API. `GET` returns the saved plans with their upcoming
dates, the active activation and the current job. Every `POST` names an
`action`; changes need `confirm: true`:

| Action | Fields besides `action` | Effect |
|---|---|---|
| `preview_plan` | `revision`, `plan` | Validates a plan and lists its next dates. Writes nothing. |
| `save_plan`, `remove_plan` | `revision`, `plan` or `plan_id`, `confirm` | Saves or removes a plan. Editing or removing the active plan stops its job. |
| `preview_activation` | `revision`, `plan_id`, `vehicle_profile`, `scope` | Returns a signed proposal for the first job and the activation end. |
| `activate` | `proposal`, `confirm` | Starts the activation within 60 seconds of the preview. Repeating it returns the outcome without extending it. |
| `deactivate` | `revision`, `confirm` | Ends the activation, stops its job and forgets the plan's remembered car. Without an activation it forgets every remembered car. |

The first job is created on activation. Each later job is created when its
window opens, replacing the finished one; until then the finished job has handed
control back (`holding` is `false`).

A `plan` has `id`, `vehicle_profile`, `timezone` (IANA), `weekdays` (Monday is
0), `departure_time`, `charge_minutes`, `current_a`, `energy_kwh`,
`ambiguous_time` (`earlier` or `later`), `enabled`, up to 32 `exceptions`
(`date` with `departure_time` or `null` to skip) and an optional `reserve_kwh`.
The activation itself is never saved; a service restart ends it. A running job
of the plan may then continue on its own as described above. Each local
date is marked as used before its job is created, so a date never runs twice,
also after an edit or restart. Plans, used dates and remembered cars are kept
in `/data/weekly-plans.json`, at most 128 KiB.

An `activate` while Home Assistant has confirmed a car for the plug-in (see
`departure_inputs` under [Departure plan](#departure-plan)) saves that car with
the plan; each entry of `plans` then reports `assigned: true`. A car handed
over as `profile:<id>:…` is remembered as `profile:<id>`, any other by its full
source text. Once the same car is confirmed for the current plug-in and no
activation exists, the service activates the plan itself, once per plug-in and
again when seven days have passed. It takes up a job of the plan that is still
running, or creates the job for the next unused date after the ordinary job
admission succeeds. A manual job stop or clear during an activation, and
`deactivate`, remove the remembered car.

Errors return HTTP 409 with a key such as `weekly_changed`,
`weekly_already_active`, `weekly_vehicle_not_confirmed`,
`weekly_confirmation_expired`, `weekly_no_occurrence` or
`weekly_storage_unavailable`; malformed input returns 400 with
`invalid_weekly_request`.

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
