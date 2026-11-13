# Service charging jobs — implementation status

This draft introduces the job model and its atomic store. It does **not** yet
connect them to the service polling loop, command API or user interface. There
is no new operational charging mode, and no existing charging behavior changes.

## Implemented contract

A request has an opaque ID, timezone-aware start and finish, a 6–32 A requested
limit and a mandatory positive additional-energy ceiling. The window is bounded
to seven days. The existing EnergyGoal validates the meter anchor, target steps,
completion and counter resets. Zero amps is rejected, never treated as a pause.

The model consumes presence, connection quality, binding match and independent
stop evidence supplied by its future owner. It does not authenticate a vehicle
or measure freshness itself. `charging` denotes model intent, not actual power
flow. The future owner must also enforce installation/device current caps.

A temporary missing meter requests a stop, retaining the original baseline.
Valid evidence may recover that same job. Completion, explicit stop, lost scope,
counter reset and clock rollback remain latched. A confirmed unplug ends the
scope even for a previously completed/stopped order. Active jobs restored from
disk become interrupted and cannot assume the same vehicle is still connected.

The store validates identity, schema and bounded size, writes a private temporary
file, synchronizes it, atomically replaces the destination and synchronizes the
directory. A failure after replacement reports an uncertain commit, not success.
It is a synchronous storage primitive: the eventual async owner must execute it
off the event loop and serialize/shield saves. No AppArmor permissions are changed
in this draft because the running service does not invoke the store yet.

## Still required before this feature is usable

Service runtime integration, serialized command admission and deferred-write
guards; stop enforcement after storage failures; authenticated UI/API routes;
packaged UI and AppArmor coverage; end-to-end runtime and restart tests.

The draft must not be presented as a finished autonomous scheduler. No physical
wallbox test or five-pass final cross-feature review has been performed.
