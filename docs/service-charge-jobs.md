# Local service charging jobs

The administrator-only **Charging jobs** page accepts one bounded order for the
currently observed connection. Set a local start/finish window, a current limit
per phase and a positive additional-energy ceiling. Preview, then confirm.
Accepted jobs run in the service, including when HA Core or the browser is gone.

This first execution layer is an explicit **energy/time** order, not an automatic
import of HA's departure or battery-percentage planner. Existing percentage and
energy stops remain authoritative. A job never authorizes RFID/Plug & Charge,
unlocks the wallbox, changes electrical installation limits or writes arbitrary
registers. It requires an existing release and valid fresh device/meter evidence.

## Ownership and stop semantics

The new service-specific control subclass uses the existing bus lock, write
floor, queue, identity verification and heartbeat. Direct HA mode is unchanged.
The exact local owner is revalidated before every enabling write, including a
queued write. Device-side EVSE/CP, error, lock and release are checked under the
bus lock. A competing HA/PV current, phase or resume command cannot override the
order. Explicit manual pause, release-off and lock commands stop the order.

Outside the accepted window, after the energy threshold, on missing meter data
or on a user stop, the service requests a priority pause through the established
energy-stop path. The hardware current-write floor still applies; 0 A is never
used to pause. A queued or unconfirmed stop is not reported as a physical stop.
The meter is polled at the existing cadence, so some energy can be delivered
between threshold observation and successful pause; this is not a hard-real-time
energy or deadline guarantee.

Only a temporary meter gap can recover inside the same known connection.
Unplug, lost/ambiguous connection, changed source binding, independent stops,
clock rollback and service restart latch an interruption. There is no automatic
restart after clearing an independent stop. An order ends its scope on confirmed
unplug and never writes to a subsequent connection. Clearing a stopped order
requires a confirmed pause or unplug and **never resumes charging**.

Physical connection continuity is not cryptographic vehicle identification.
The public scope digest is an opaque, changing confirmation token, not a VIN.
A service restart cannot prove continuity and restores an active job as
interrupted. An HA-only restart does not restart the service or its job.

## Persistence and permissions

The optional `/data/charge-job.json` uses the identity-checked, bounded atomic
store. Permission is persisted before starts; ordinary meter progress remains
in RAM. A failed or unconfirmed save fences starts, while a known stop is still
attempted. A corrupt store is visible and requires an explicit safe reset.
Worker-thread saves are shielded against cancellation; mutable observations
cannot silently turn an older saved snapshot into permission for a new start.
AppArmor permits only the named job file and its private same-directory
`.job-*` temporary files, not general writable data paths.

## API

`GET /v2/jobs` and `POST /v2/jobs` inherit the existing bearer authentication and
4 KiB request limit. Ingress uses `/api/jobs`, the Supervisor peer/admin boundary
and the established CSRF token. No new public port is opened. Each mutation uses
an optimistic revision; creation also confirms the current scope.

Preview/create payload:

```json
{"action":"preview","revision":"from GET","scope":"from GET","request":{"id":"32 lowercase hexadecimal characters","start_at":"2026-11-13T22:00:00+00:00","finish_at":"2026-11-14T06:40:00+00:00","current_a":12,"energy_kwh":10}}
```

Confirm the canonical `request` returned by preview using `action: "create"`.
Replaying that exact existing request returns its outcome without resetting the
energy baseline or rearming a terminal job. Stop/clear use `action`, `revision`
and `confirm: true`. Browser POSTs are not retried automatically after a lost
reply; read the current state first.

`job.state` is intent, not measured vehicle state. Use the overview and command
outcomes for actual readback. The new page includes no credentials, serial
number, source entity or raw transport/storage exception. Five final cross-
feature review passes are separate from these per-PR implementation checks.
