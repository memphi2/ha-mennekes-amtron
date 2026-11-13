# Adaptive AC-energy completion forecast

The existing **Charging jobs** page shows a conditional time range to the
accepted job's **AC-energy ceiling**. It does not predict battery SoC, usable
battery energy or the moment the vehicle will decide to stop. No battery
capacity, nominal charging power or efficiency is guessed.

The forecast is advisory. It cannot write current, grant release, modify a
job, override a pause, extend a deadline or remove an independent target.
Its data is included in the existing authenticated job GET response. There
is no additional endpoint, browser polling loop, Modbus read, vehicle/cloud
request or Recorder attribute.

## Learning and sample quality

Learning uses actual changes in the lifetime meter, with the existing
service-side observation timestamp and monotonic elapsed time. Valid intervals
span 30–180 seconds. The connection, confirmed current setpoint, selected
source and effective phase context must remain unchanged. The latter combines
the selected relay configuration, known grid wiring and the vehicle maximum;
a single-phase selection is not mistaken for a three-phase vehicle maximum. At least three valid intervals
are required; at most eight live intervals are retained.

Repeated cached reports cannot renew freshness. Missing readings, changed
context, pauses, resets, backwards clocks and long gaps discard live evidence.
Counter jumps are rejected using a deliberately loose current/phase-scaled
plausibility ceiling (300 V per known phase, globally no more than 30 kW).
That bound is input validation, not an assumed operating voltage or a safety
control. Unknown phases do not become an invented three-phase power estimate.

A live estimate must match the current meter value **and timestamp**. An HTTP
read cannot combine a newly polled counter with an older learning frame. Finish
arithmetic is anchored to that observation, so repeatedly opening the page
does not push the expected finish forward. A predicted finish already in the
past produces a wait-for-meter state, not a claim that energy was delivered.

## The displayed range

The typical power is the median measured rate. The conditional range uses the
observed slowest/fastest retained rates with a 15% margin. This is an engineering
range, **not a calibrated statistical confidence interval or a guarantee**.
Large variability, insufficient progress and projections beyond seven days
suppress a precise end time. Future tapering, temperature changes, external
curtailment and unknown future interruptions can invalidate the estimate.

The calculation includes the already accepted start window, reserve portion
and boost without counting early energy twice. The target remains the same
single energy ceiling. The UI distinguishes live measurements from historical
measurements and indicates whether the range fits the existing finish limit.
Other vehicle, energy or SoC limits may stop charging earlier.

## Bounded history and privacy

A source/current/phase-specific history can provide a conditional estimate
while a confirmed order waits for its time window. It is never substituted
for missing live evidence while a vehicle is expected to be charging.
History expires after 30 days, holds at most 64 contexts and 16 rates per
context, and never crosses known current or phase changes.

The grouping key hashes the configured SoC source or the explicitly confirmed
weekly-profile reference. It is not proof of vehicle identity or guaranteed
anonymization. Guest, unknown-source and ended/changed-connection observations
can have a live estimate but cannot train the previously selected vehicle's
persistent history. Raw vehicle identifiers and source/entity names are not
stored or sent in the forecast. The private store is bound to the wallbox's
identity, as are existing service stores.

`charge-rates.json` is limited to 64 KiB, uses private `.rates-*` temporary
files, atomic replacement and file/directory fsync. At most one save worker
exists. Writes are coalesced to at most once per ten minutes, plus a final
shutdown flush. Unchanged history is not rewritten. Shutdown stops the
charging/bus owners before joining the optional writer.

Corrupt, missing or unwritable advisory history does not disable charging,
change a job's storage status or restart the bus. A bounded warning and a UI
notice identify the unavailable history; fresh learning may continue in RAM.
AppArmor adds only the named file and its temporary prefix, with no new public
port, general directory write permission or privilege.

## Validation

Calculation, context/freshness, persistence and corruption tests exercise the
proposed modules. Offline frontend tests execute the shipped JavaScript and
check text-only rendering, old service responses, hidden pages and stale data.
Service/fake-Modbus, authenticated HTTP and lifecycle regressions verify the
runtime integration in CI. Physical wallbox and live Supervisor validation
remain separate; exact commit/run results are recorded on the pull request.
