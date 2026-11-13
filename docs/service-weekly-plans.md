# Service-owned weekly charging plans

The administrator-only **Weekly plans** page saves recurring vehicle recipes,
previews their local dates and activates one recipe for the currently confirmed
physical connection. The bearer API exposes the same bounded operations at
`/v2/weekly`; Ingress uses `/api/weekly`. The existing authentication, request
size, CSRF and single-Modbus-owner boundaries are unchanged.

## Save, inspect, activate

A recipe contains a stable vehicle-profile reference, explicit IANA timezone,
weekdays (Monday=0), departure time, elapsed charging window, current ceiling
and additional-energy ceiling **per departure**. Up to 16 recipes are stored,
with one recipe per profile. Up to 32 date exceptions can skip, move or add a
single departure. Unknown fields and invalid/nonfinite input are rejected.

Saving is not authorization to charge. Inspect the preview, save the recipe,
select the saved profile and confirm that this vehicle is connected. The
activation preview shows the first absolute job and the consent expiry.
Confirmation expires after 60 seconds and cannot be edited or extended by a
lost-response retry. The recurring consent lasts only for this connection,
for at most seven days, and is never written to disk.

The profile reference is an administrator-confirmed label, **not automatic or
cryptographic vehicle identification**. Existing local authorization, release,
current cap and independent energy/SoC limits remain authoritative. The page
never unlocks or grants release. This is an energy/time recipe, not a promise
to reach a percentage target or an automatic import of an HA vehicle timer.

## Scheduling and stop priority

The first absolute order is accepted by the existing durable service job
controller; it pauses until the configured window and executes without a
connected HA/browser client. The weekly loop can replace it with the next
occurrence only after a successful completion/window end, the previous
scheduled departure has passed, and a fresh paused readback is present.
The replacement is atomic within the job-owner lock: there is no clear/create
gap that could lose a manual stop.

Each successor receives its own explicitly previewed per-departure energy
ceiling and a fresh meter baseline. An interrupted/stopped job is never a
successor source. Manual stop or clear also ends recurrence after a job has
already completed. Disconnect, changed connection scope, independent stop,
clock rollback, consent expiry and service restart end activation. A later
recovery or unchanged recipe cannot automatically rearm it.

Editing/removing an active recipe stops its own accepted job and recurrence.
Other vehicle recipes do not affect the current owner. A pre-existing guest
or manual job is never taken over. HA shutdown cancellation of an unrelated
HA-owned timed command does not cancel the service-owned job.

## Timezones and replay protection

Nonexistent local times are visible invalid rows, not shifted departures.
Ambiguous times use the explicit earlier/later choice. Both folds are checked
by UTC round trips, including half-hour transitions and deleted dates.
Charging windows are elapsed time, not local-clock subtraction.

Before admitting an occurrence, the service atomically saves a consumed local
date. The monotonic high-water date survives recipe edits, deletion and restarts;
changing time, profile, current or energy does not erase it. Dates at or before
that watermark cannot run again automatically. Consumption is intentionally
at-most-once: a crash after claiming a date but before admitting its job may
skip that departure instead of creating a second energy baseline. Inspect the
consumed row and current job before explicitly creating a replacement order.

The ledger retains up to 128 recipe-ID tombstones, without silent pruning.
Exhaustion or unconfirmed storage fails closed for weekly activation. Recipes
and ledger use `weekly-plans.json`, bounded to 128 KiB, private `.weekly-*`
temporary files, atomic replacement and file/directory fsync. They are bound
to the wallbox identity. A failed/uncertain save is not reported as permission.
After storage errors, restart and inspect the state before a new activation.

## Interfaces and limits

Actions: `preview_plan`, `save_plan`, `remove_plan`, `preview_activation`,
`activate`, `deactivate`. Mutations require confirmation; recipe edits use
optimistic revisions. Activation is additionally bound to the exact job
revision, physical scope and a signed absolute preview. Browser POSTs are
never automatically retried. Detailed raw credentials are not returned.

The new page is German/English, supports narrow layouts and inherits the
existing light/dark palette. It stops requests in hidden tabs and discards
obsolete responses. No extra periodic Modbus reads are introduced. The
AppArmor changes allow only the named weekly file and temporary-file prefix.

Real start/stop timing is still limited by normal polling, the control queue,
write floors and hardware responses; it is not hard real time. A successful
model decision is not a confirmed physical switching result. Minimum reserve,
adaptive estimates, PV/tariff optimization and the persistent multi-session
journal remain separate roadmap features.

## Validation

Focused local tests exercise the actual calendar, book, job and recurrence
modules, filesystem failure injection, manual-stop epochs, signed previews,
concurrent confirmation and cancellation. Frontend protocol tests execute the
shipped JavaScript. Real-service/fake-Modbus tests and authenticated HTTP tests
are included for the repository's Python 3.14/HA CI. No physical wallbox or live
Supervisor test is claimed. CI status is recorded on the PR for its exact head.
