# Temporary early charging (Boost)

The **Charging jobs** page can temporarily start an accepted energy/time order
before its scheduled start. Enter 1–120 whole minutes, preview the exact end
time and confirm. The end is clipped at the original start so the normal plan
takes over directly when its window opens. **Return to plan now** ends only the
boost; **Stop automation** ends the whole order and cannot be undone by expiry.

A boost retains the original start/finish, current ceiling, meter baseline and
energy ceiling. Energy delivered during the boost therefore reduces the energy
remaining for the normal window. It does not increase a service/installation
limit, grant RFID/Plug & Charge, unlock a wallbox, change phases or override an
independent percentage/energy stop. It requires fresh evidence for the same
physical connection and no competing pending command.

The existing service scheduler, command queue, pre-write guards and heartbeat
are reused. At expiry, an enabling write already queued is no longer permitted,
even before the next scheduler tick. The actual start/stop remains subject to
the existing hardware write timing; the displayed end is an intent deadline,
not a promise of an instantaneous physical switch.

Boost permission is saved atomically before enabling charging. Missing meter
data pauses it without extending the end time. A failed save, user/goal stop,
connection change, clock rollback or service restart cannot silently rearm it.
Old job files without a boost remain readable. Downgrading to code that does not
recognize an active boost fails closed rather than interpreting it as a grant.

## API

Both authenticated `/v2/jobs` and admin/CSRF-protected `/api/jobs` use the same
operations. `preview_boost` takes `revision`, `scope` and integer `minutes`.
Confirm the returned absolute `until`, `revision` and `scope` with `action:
"boost"`. `cancel_boost` takes `revision` and `confirm: true`. Extra fields are
rejected. A repeated old confirmation is rejected rather than sliding a
relative timer. Browser POSTs are never automatically retried after a lost reply.

This step boosts the explicit service-owned energy/time plan. It does not take
over an independent HA-only planner or identify a vehicle. Weekly schedules,
minimum reserve and adaptive completion forecasts are separate enhancements.
