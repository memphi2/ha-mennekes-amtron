# Guest charging

The administrator-only **Guest charging** page is a preset for one immediate,
service-owned energy/time order. Enter an additional-energy ceiling, a maximum
duration of 1–1440 whole minutes, and a current within the effective service cap.
Preview the absolute start/end times, then explicitly confirm. The preview does
not write to the bus or storage. Confirm it within one minute; an old proposal
must be previewed again rather than silently moving its deadline.

This is **not a new authorization method**. The guest vehicle must already be
connected, the meter current and the wallbox released (for example with local
RFID). The service does not unlock, grant release, disable RFID or bypass an
existing stop. An active vehicle SoC target blocks guest creation instead of
being silently inherited or cleared. Explicitly disable that target in HA first.
If a vehicle target is armed later, the pre-write guard fences guest starts and
the running guest order becomes interrupted. Removing that target does not
rearm the interrupted guest order.

A guest order stores its explicit `kind=guest`, original meter baseline and
absolute time window. It has the same durable stop/energy/deadline behavior as a
normal order. No VIN, battery level or remembered vehicle target is imported.
It owns only the current physical connection. A confirmed unplug ends that
scope; reconnecting or restarting the service cannot continue the old order.
Repeated confirmation of the same accepted payload returns the existing result
without resetting the meter baseline or extending the deadline.

The existing authenticated `/v2/jobs` and administrator/CSRF-protected `/api/jobs`
interfaces accept `preview_guest` with a request containing `id`,
`duration_minutes`, `energy_kwh` and `current_a`. Confirm the returned absolute
request through the existing `create` operation. The same stop/clear controls
apply. Clearing a stopped job never resumes charging. No new port, poll loop,
public endpoint, AppArmor permission or persistent store is introduced.

The page reports the currently observed energy progress and requested state,
not a confirmed physical stop or guaranteed delivered energy. It deliberately
has no automatic POST retries. A persistent multi-session journal is a separate
roadmap step; this PR does not claim to provide that history yet.
