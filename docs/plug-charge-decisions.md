# Why Virtual Plug & Charge is waiting

The existing status values and `blocked_reason` attribute keep their meaning.
The status sensor additionally exposes `decision_reason`, including waits which
are not wallbox faults. Read it with
`state_attr('sensor.<wallbox>_virtual_plug_charge_status', 'decision_reason')`.
Use your actual entity ID.

| Reason | Meaning / next check |
|---|---|
| `detection_not_configured` | No vehicle profile has detection signals. Configure cable and home evidence. |
| `multiple_vehicles_match` | More than one configured car matches. Resolve the contradictory signals. |
| `vehicle_evidence_unknown` | At least one participating car cannot be excluded. Check all profiles, not just the selected one. |
| `no_vehicle_matches` | No configured vehicle currently matches the cable/home evidence. |
| `vehicle_binding_pending` | The selected vehicle's targets or binding are not ready for this connection. |
| `session_time_unknown` | The bus owner supplied no usable connection start time. |
| `plug_report_not_current` | Cable evidence is missing or predates this connection's allowed window. |
| `settling` | Evidence passed; the ordinary settle timer/authorization is pending. |
| `grant_retry` | The last authorization attempt failed; ordinary retry cadence still applies. |

Other reasons mirror the established status or `blocked_reason`, including
`connection_lost`, `departure_plan`, `release_off`, `energy_goal_stop`,
`wallbox_locked`, `authorization_required` and `register_unavailable`.

## Downloaded diagnostics

The `plug_charge.decision` section adds all simultaneous release blockers,
missing register names, the relevant departure-plan status, a sanitized category
for the last failed grant, and per-profile cable/home checks. `slot` is only the
position in this snapshot, not a persistent vehicle ID; `selected` identifies
the active profile. `matches` is true, false or null; `report_current` separately
checks whether the report belongs to this connection.

`cable_unknown_or_stale` and `location_unknown_or_stale` deliberately do not
pretend to distinguish missing from stale data: providers expose both as null.
A parked-home observation can still match when its cable-change time agrees
with the wallbox connection, exactly as before.

The snapshot includes no VIN, coordinates, entity/profile IDs, authorization
tokens, policy hashes, raw exception messages or ticking age counters. Only the
compact decision reason goes into the sensor attributes; detailed evidence is
collected on diagnostic download. Successful authorization and a new session
clear the previous grant error.

These are explanations, not new permissions. These fields add no commands or
extra bus reads; the standard diagnostic download retains its existing raw
register snapshot. Existing first-blocker priority, authorization checks,
settle/retry timing and independent stops remain unchanged. Once authorized, a
session may remain authorized despite old cloud evidence; the physical bus
owner still controls its lifetime. Consequently, a later diagnostic evidence
snapshot explains current signals, not proof of what happened at grant time.
