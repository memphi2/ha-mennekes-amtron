# Audit: failures and restart (0.9.0b2)

Date: 2026-11-13. Scope: the Modbus service app with all its controllers
(charging jobs with boost and immediate reserve, guest orders, weekly plans,
departure plan, energy and percentage targets, Virtual Plug & Charge, manual
pause) and Home Assistant beside it. Method: reading the code paths for each
situation, with targeted probes against the real controllers; no hardware.

The question for every situation: can the car end up charging although a stop
was required, paused with nothing that will resume it, charged twice or with a
skipped departure, shown in a state that contradicts the app, or in a retry
loop on the bus or the disk?

## Situations examined

| Situation | Behaviour | Result |
|---|---|---|
| App stops or is killed while an order charges | The order is restored as suspended and continues only on proof of the same plug-in; otherwise it is interrupted and pauses | ok |
| The same with Virtual Plug & Charge enabled | The gate closes the release at start; the proven order was then interrupted | fixed (1) |
| App restart with a paused or charging departure plan | Pause ownership, charge-level anchor and plug-in proof are restored | ok |
| App restart with a queued command | The queue is not saved; a queued resume of the plan was lost for good | fixed (3) |
| App restart with a reached kWh or percentage target | The latch and the pause are saved and enforced again | ok |
| Home Assistant offline for hours | Orders, plan and gate keep running; the plan projects from the meter | ok |
| Home Assistant returns with another car or none | The plan rests and ends its own pause | ok |
| Departure planning switched off for the selected car | The app kept planning the trip | fixed (2) |
| Link drop of 10 s | Orders and plan write nothing and continue | ok |
| Link drop of 10 s with a kWh target | One failed poll paused the charge | fixed (4) |
| Link drop of 5 min | An order suspends after a minute and needs proof; the plan keeps its start | ok |
| Car unplugged or swapped during a gap | Session counter or clock mismatch interrupts the order | ok |
| Wall clock jumps back more than 5 s | The order ends and a weekly activation is withdrawn | ok, documented |
| Small step back or forward step over 90 s | The meter reading counted as old and a running order paused | fixed (5) |
| Exception in a controller's tick | Caught per loop; writes stay fenced | ok, log flood fixed (6) |
| Disk write fails | Reported as a lost wallbox | fixed (7) |

## Findings and what changed

1. **An app restart with Virtual Plug & Charge ended every running order.**
   The gate's grant is not saved, so the release is closed at start; the order
   took that as someone switching the release off. An order now waits while
   only the gate's release is missing and continues once Home Assistant grants
   it again. A release switched off by hand, a lock or a wallbox error still
   end it.
2. **Switching departure planning off left the app's plan running.** Home
   Assistant now withdraws the trip from the app when the selected car no
   longer uses departure planning, as it does when another car is selected.
3. **A resume that was only queued could be lost.** The plan gave up its pause
   as soon as it had asked for the resume. It now keeps the pause until the
   wallbox shows that it ended, and asks again after a restart.
4. **One lost poll paused a charge with a kWh target.** The target is told
   about a missing meter only after the bus failed for a minute, the grace an
   order already had.
5. **A wall-clock step paused a running order.** The age of the meter reading
   is measured on the monotonic clock.
6. **A persistent failure of the weekly tick logged a traceback every
   second.** It is logged once and retried every five seconds.
7. **A failing save of the control intent looked like a lost wallbox.** It is
   logged and the wallbox stays in use.

## Residual risks, not verified

- Continuation after a restart assumes that the wallbox's session duration
  keeps running while paused and that its session counter and session energy
  survive as documented. This needs a check on real hardware.
- On a host without a real-time clock the app can start before the time is
  synchronised; the proof of the same plug-in then fails and the order is
  interrupted, which is the safe direction.
- After a power cycle of the wallbox with a car connected, a manual pause or a
  settled order's pause is not written again when the wallbox comes back with
  a charging current and without error 200. Target stops, holding orders and
  the departure plan do pause again.
- A wallbox error code other than zero with a car connected still ends a
  running order.
- An exception type the poll does not expect ends the service process and
  relies on the supervisor's restart.
