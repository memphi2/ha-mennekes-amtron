# Weekly charging plans — calculation foundation

**Status: calculation and proposal generation only.** This change does not yet
add a running weekly scheduler, saved plan management, an HTTP endpoint or a
service page. It must not be described as a completed weekly charging feature.
It does not change the existing runtime, authorization policy or Modbus traffic.

## Input contract

`WeeklyPlan.parse()` validates a recipe with:

- A stable 32-character hexadecimal plan ID and an opaque vehicle-profile ID.
- An explicit IANA timezone and unique weekdays (`0` = Monday, `6` = Sunday).
- A local `HH:MM` departure and a configured elapsed charging window of
  1–1440 minutes. This is not a learned completion-time estimate.
- A 6–32 A current ceiling and a positive additional-energy ceiling validated
  by the existing `EnergyGoal` implementation, including its 0.1 kWh steps.
- An enabled flag, an `earlier`/`later` ambiguous-time policy, and at most
  32 unique date exceptions. An exception either skips a date or replaces/adds
  that date's departure time.

Unknown fields, naive timestamps, booleans in numeric fields, numeric strings,
invalid dates and unavailable timezone data are rejected. The host timezone is
never substituted for a missing timezone. Snapshots are canonical JSON data.

## Time handling

`upcoming()` provides a bounded preview covering the current local date through
at most 31 days ahead, with at most 32 rows. Real departures at or before `now`
are excluded. Skip and invalid-time rows are visible and cannot become jobs.

Both folds are round-tripped through UTC. A nonexistent local time is not moved
silently: the row is marked `invalid_time`. A repeated local time produces one
occurrence according to the explicit fold policy. Passing the earlier instant
does not silently change an `earlier` plan into a `later` plan. Durations and
instant comparisons use UTC, including across half-hour clock changes.

The implementation follows the standard library's timezone/fold semantics:
https://docs.python.org/3.14/library/zoneinfo.html
https://docs.python.org/3.14/library/datetime.html

## Proposal versus permission

`WeeklyOccurrence.job_request()` freezes one absolute request matching the
existing service job's input shape. A late preview clips the start to the
preview time, but does not extend the departure. The finish must be within the
next seven days. The current meter baseline is deliberately **not** calculated
here; it belongs to the existing controller's accepted session transaction.

The returned proposal must be retained unchanged for confirmation. Rebuilding
it on every retry can change its start time. A profile-name match is not proof
of the physical vehicle's identity. Existing revision, session, current-cap,
meter, independent-stop and authorization checks still have to run at admission.

An occurrence ID stays stable across same-day edits to time, current, energy
and fold choice. This aids correlation but is **not** a substitute for a durable
consumed-occurrence ledger. The model contains no manual-stop reset or rearm
operation and performs no network, filesystem or bus operations.

## Required before completing this feature

Persist plan configuration and consumed occurrences with the existing uncertain-
commit safeguards. Bind activation to a freshly confirmed vehicle/session. Add
an owner-controlled scheduling loop that does not supersede guest jobs, manual
stops or independent goals. Provide authenticated preview/confirmation and
explicit skip/override controls in the service UI/API. Test the complete flow,
including restarts, connection changes, same-day edits and lost HTTP replies.
These are not implemented by this foundation patch.

## Validation scope

158 focused pytest cases passed under Python 3.13.5 using the actual new module
and the unchanged `EnergyGoal` source from the repository artifact. The new
module reached 189/189 statements and 60/60 branches in that isolated run.
This is neither whole-project coverage nor an HA/service integration test.
Python compilation and patch-application checks also passed.

The repository's Python 3.14 CI separately runs the complete validation suite,
including Ruff and strict mypy gates. Commit-specific outcomes are recorded in
the pull request, rather than treating a focused local run as a full CI result.
No physical wallbox, Supervisor or browser runtime test has been performed for
the new weekly-plan foundation.
