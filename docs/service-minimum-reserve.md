# Immediate minimum-energy reserve

Scheduled service jobs and weekly recipes accept optional `reserve_kwh`.
It is a **meter-measured additional AC-energy portion**, not a battery SoC
percentage or a guaranteed amount of usable driving energy. No battery state,
capacity or charging efficiency is invented or inferred from the AC meter.

For example, a job with a 20 kWh ceiling, a 5 kWh reserve and a later start:

1. Requests its configured current immediately, subject to existing permission.
2. After the meter reports 5 kWh since the original baseline, requests pause.
3. Resumes at the original scheduled start, with only the remaining portion of
   the same 20 kWh ceiling available. It never adds another 20 kWh allowance.

Zero or omission disables the reserve and preserves legacy request/storage
representation. Positive values use the existing 0.1 kWh steps, may not exceed
the total job ceiling and must be representable by the actual meter baseline.
Guest jobs cannot acquire this early phase.

## Control and persistence

A reserve opens only an earlier part of an already explicitly accepted job.
It does not grant authorization, release the wallbox, unlock it, bypass current
caps, reset independent energy/SoC stops or create another bus owner.

Meter freshness and all normal guards are still checked before every enabling
write. A reserve threshold observed between scheduler ticks also prevents a
new early-current write. Missing meter readings pause the order; a recovered
reading may continue only the same still-valid connection. A reset, manual
stop, connection ambiguity or service restart cannot rearm it.

The reached-reserve latch is stored atomically with the original job and meter
baseline. It cannot be undone by a lower later reading, a lost HTTP reply or a
repeat confirmation. Storage failures still request a pause and fence starts.
A configured boost can explicitly charge beyond the early reserve, but never
beyond the original total ceiling; cancelling it returns to the current plan.

Weekly recipes carry the same reserve into each independently consumed
occurrence. Each occurrence has its own total ceiling and baseline, as already
shown by weekly activation. A successor's reserve may therefore start as soon
as that successor is accepted, before its scheduled charging window.

## User interface and compatibility

The existing **Charging jobs** and **Weekly plans** pages contain the optional
reserve field, explain that it is included in the total, and show it in the
confirmation. The job status shows whether the reserve has been reached.
Editing inputs invalidates a preview; failed POSTs are not automatically
retried. Existing bearer and administrator/CSRF boundaries are unchanged.

No new endpoint, listener, store, AppArmor permission, Modbus read or Recorder
entity is added. Older installations without a reserve retain their exact
storage shape. Do not downgrade while a reserve job or recipe is stored:
older code rejects unknown fields rather than silently granting permission.

Physical switching still obeys polling, the command queue and device write
floors. The reserve is not an instantaneous hard stop or a promise that the
vehicle will accept energy. A battery-percentage reserve would need a separate
fresh, vehicle-bound SoC contract; this implementation does not substitute an
AC-energy estimate for that contract.

## Validation

Pure model/controller tests cover transitions, validation, meter outages,
round trips, latching, replay and weekly propagation. Offline protocol tests
execute the actual shipped JavaScript. Real-service/fake-Modbus and bearer-API
regressions are included for full CI. No physical wallbox or live Supervisor
test is claimed. Exact CI results are recorded on the pull request.
