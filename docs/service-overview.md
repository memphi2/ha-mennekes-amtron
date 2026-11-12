# Service overview (step 1)

The app opens an administrator-only **Overview** through existing Supervisor
Ingress. Connection settings remain under **Connection**, and **Debug log**
opens the existing optional trace. The overview is passive: it cannot start,
stop, unlock or reconfigure the wallbox.

It shows observed charging state separately from service intent, heartbeat
health, current power, session energy, the read-back current setpoint, saved
targets, the next queued action and up to 32 command outcomes since startup.
A zero-amp setpoint is labelled unlimited, not paused. A queued action and its
earliest execution time do not establish that it has executed.

The endpoint reads in-memory snapshots only. It performs no extra Modbus reads,
writes or persistence. Held/unavailable readings are not shown as current.
A failed browser request hides old data. The page refreshes every five seconds
while visible; background tabs stop requests and discard obsolete responses.

No service credentials, serial numbers, vehicle identities, raw exceptions or
boot tokens reach this endpoint. It retains the existing Supervisor-peer and
administrator boundary. Standalone API routes and public ports are unchanged.

The page does not invent vehicle identity, current battery level or a full
HA-owned departure plan. Saved targets are limits, not progress. Command
sequence numbers are not timestamps, and the outcomes are not a persistent
charging journal. Step 1 does not introduce service-owned scheduling or new
charging controls.

Five UI languages, light/dark styling and responsive layouts share the existing
app assets. The wheel manifest and installed-container asset checks cover the
new resources. Tests exercise projection, privacy, missing/stale values and the
authenticated HTTP boundary. No physical wallbox or live Supervisor validation
is claimed by these simulations.
