# Departure charging: the last control window

The departure planner still starts late, with its existing reserve, and uses
full current while it needs it. Near the end it now considers the next
five-minute interval before the observer may change the current again.

An immediate jump between maximum current and 6 A can overshoot the point at
which 6 A would finish at departure. Instead, the planner calculates the energy
above that minimum-current trajectory and spends only that excess in the next
control window. The intermediate request is rounded upward to whole amps, to
match the observer's 1 A change threshold, and never exceeds the configured
current cap. Once 6 A suffices, it remains the finishing current. A final
partial window uses the actual remaining time, not a full five minutes.

The exact 6 A boundary is included in finishing. Less than four milliseconds
of timestamp/float rounding is tolerated at the minimum- and maximum-current
boundaries; this is not a reserve for late telemetry.

## Limits

This improves the calculation, not the freshness of vehicle data. The existing
230 V and 90% battery-efficiency assumptions, usable battery capacity, detected
phases, start reserve, initial pause margin, switching guards and independent
charge targets still apply. No learned efficiency model, extra vehicle requests,
extra Modbus reads or new automatic-release policy is introduced.

A slow vehicle, battery taper, inaccurate capacity, delayed SoC reports or a
hardware downgrade can still move the actual finish. The simulation tests use
fresh progress and the same power model and are not a hardware guarantee.
When a charge is already too far ahead, the planner does not go below 6 A or
introduce repeated pauses merely to consume the remaining time.

The finishing window and the observer's write floor are checked together by
`tests/test_charging_plan_cadence.py`. Changes to either need a joint review.
