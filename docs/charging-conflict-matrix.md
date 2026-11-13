# Charging conflict regression matrix

`tests/test_charging_conflict_matrix.py` adds cross-feature sequences, not a new
priority policy. The same input sequence runs directly and through the actual
authenticated HTTP service with a fake Modbus transport. No real wallbox is used.

| Sequence | Required outcome |
|---|---|
| Two independent stops; remove either first | Both attempts remain refused until every stop is removed. No write, including no release pulse, is permitted. |
| Last stop removed | Removal alone sends no command. An explicit fresh grant writes only the release; a deliberately paused current remains paused. |
| Plug & Charge grant; departure resume queued; kWh or SoC goal reached | The queued start is cancelled with the energy-goal reason, without another current write. The goal remains independent of the gate grant. |
| Reached goal explicitly replaced after cancellation | The cancelled command is not replayed and the pause remains. |
| Departure decision pending, then applied; new manual/goal stop during settling | The captured old timer cannot authorize. Clearing the stop requires a fresh settle timer and still does not lift the plan's pause. |

The pair matrix covers all ten combinations of manual release stop, wallbox
lock, local RFID requirement, wallbox error and reached kWh goal, in both removal
orders and on both connection paths (40 cases). Queue/goal sequences add four
cases; observer/timer sequences add six. Existing low-level recovery, expiry,
transport uncertainty and restart tests remain in place.

The test harness supplies valid goal models and simulated register values. It
intentionally stops the fixture's fast heartbeat when checking queue timing;
heartbeat behavior is covered by its existing dedicated tests. These tests do
not establish physical safety, cloud freshness or hardware compatibility.
