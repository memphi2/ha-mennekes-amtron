# Maintenance validation

Run the normal suite with `.venv/bin/python scripts/check_validate.py`.
The minimum HA environment uses `--skip-typing`; latest stable HA
also runs strict typing. These checks do not access a physical wallbox.

## Fault regressions

The suite includes failures that a successful normal charging cycle does not
exercise:

- A newer current write can reach the wallbox even if its reply is lost or its
  caller is cancelled. Tests verify that an older queued value is discarded,
  the latest intent is saved, and a service restart followed by pause/resume
  does not bring the older value back.
- A failed status-register read must retain an existing heartbeat or fallback
  repair. Only a valid healthy reading clears it, without repeatedly deleting
  and recreating the issue.
- Error 200 can disappear during recovery's fresh precondition read and return
  in its final poll. Automatic recovery keeps a delayed retry for that race.
- Invalid optional device text must not abort initialization or polling;
  unused bytes after the NUL terminator are ignored.
- A service response within the byte-size limit can still exceed the JSON
  decoder's nesting limit. The HTTP client reports invalid data and marks the
  connection unavailable instead of retaining a usable cached connection.
- Replacing the wallbox behind the same USB/LAN endpoint must not authorize
  writes with cached identity. Tests change the simulated device registers,
  exercise reconnection and an already-open link, and verify rejection without
  changed intent, followed by recovery when the original device returns.

These tests use real decoders, the shared control/poll paths and simulated
Modbus or HTTP responses. They complement hardware tests; passing them does
not establish that every possible device or timing fault has been covered.

## State-change queue — next beta, unreleased

The queue's regression contract covers configurable intervals, the unchanged
five-second current-write floor, and a shared guard for pause/resume, phase
changes, both charging-release directions and lock/unlock. Tests must also cover
repeated identical commands, newer requests superseding pending work, a queued
stop replacing a pending start, and same-intent recovery while the longer
interval is active.

Phase-switch tests must prove that the pause command alone does not authorize
the phase write: live status must first confirm the paused state. Cover delayed
confirmation, invalid or failed reads, timeout and cancellation without a phase
write or automatic resume. Verify the configurable grace time (default 5 seconds,
range 0–30), the live recheck after that wait and the independence of the shared
state-change interval. Zero grace time must not skip pause confirmation.
Connected vehicles require EVSE state 2 and, when the layout provides it, CP
state B1. An idle EVSE without a vehicle needs neither confirmation nor grace
time. Test the corresponding register-layout and idle paths. The long-interval
exception requires fresh Idle plus CP A1/A2, never cached state alone. Cover
all command directions, failed/invalid/disagreeing reads, old layouts without
CP, unplugging during a pending wait, replugging before execution, and the
unchanged five-second write floors. Pending timestamps must stay stable.

Use a controlled clock for delay and cancellation cases. Check that a worker
does not hold the command lock while waiting, revalidates execution, and cannot
send stale commands after shutdown or a newer request. Service-mode checks
must distinguish an HA restart from a service restart: only the latter stops
the service-owned queue. Configuration bounds and optional API 2 status fields
need their own invalid-input checks.

The two status entities must publish a stable action and due timestamp during
timed waits. While awaiting vehicle pause confirmation, the phase action stays
visible with no due timestamp. Confirmation sets the fixed grace-end timestamp.
Repeated snapshots and elapsed seconds must not generate countdown state updates
in Recorder. These software checks do not replace hardware
acceptance of deliberate pause, resume and phase switching.

## Coverage requirements

The coverage gate evaluates statements and branches together, using the
integer counters in its JSON report:

- At least **99% overall** for the integration.
- Strictly **more than 95% in each production integration module**. Strong
  coverage elsewhere cannot hide an untested module.
- Exactly **100%** in the configuration-flow modules: `config_flow.py`,
  `options_flow.py`, `_flow_connect.py`, `_flow_search.py`, `_flow_serial.py`
  and `config_schemas.py`.

The thresholds use unrounded counts; a displayed percentage rounded up to a
threshold does not pass. Missing or invalid reports and missing production
modules fail the check. The test output identifies remaining statements and
branches. These are enforced targets, not a claim that every module has no
coverage gaps or that simulated tests replace hardware verification.

## Resource measurements

```bash
.venv/bin/python scripts/check_runtime_soak.py --cycles 30 --idle-seconds 10 --json-output /tmp/amtron-soak.json
```

The isolated Linux process boots real HA with synthetic Modbus replies. After
two warmup cycles it measures loaded, recovered, idle and unloaded phases:
Python allocations, resident memory, tasks, timers, listeners, file descriptors
and retained clients. Each cycle exercises connection failure/recovery and
unloading with an active heartbeat and a deferred current write. Idle time is
distributed across the measured cycles. Separate real serial open/close cycles
verify exclusive port release and zero descriptor growth.

`scripts/check_runtime_soak.py` defines the growth budgets. Measurements compare
equivalent phases; unloaded entries must leave no owned tasks, listeners or
open clients. Listener counts must not grow in any measured phase after warmup,
including continuous operation; a temporary peak also fails the check.
A successful subprocess without a valid report cannot pass.
CI runs 30 cycles, while the ordinary suite uses three for a quicker regression
check. The serial simulator also closes its relay threads and descriptors.
This bounded stress test does not replace physical-wallbox validation, a
wall-clock soak lasting days, or the separate Recorder retention tests.

For continuously loaded operation, run:

```bash
.venv/bin/python scripts/check_runtime_steady.py --seconds 3600 --json-output /tmp/amtron-steady.json
```

This separate test uses real elapsed time, a real Modbus RTU client and server
connected through virtual serial ports, and active polling and heartbeat timers.
It closes and reopens the actual serial transport every 30 seconds without
reloading the entry. Measurements change while delayed replies and concurrent
reads exercise the bus scheduler. Every delivered-heartbeat gap, including a
silent tail at the end, must stay within ten seconds. It measures the same
resource budgets approximately every five seconds and checks
cleanup afterwards. The normal suite exercises a short version; scheduled
and manual CI runs measure one hour on latest stable HA. This adds no required
HA version to the minimum/latest matrix. The wallbox register values are simulated; it
does not replace hardware tests.

The same run also starts real Recorder with a disposable SQLite database and
full history. On each reconnect, Recorder concurrently purges old test history
and repacks the database. The test observes actual `DELETE` row counts and
`VACUUM` completion, verifies database integrity, and checks that energy and
power readings remain recorded. A short history window (up to 15 seconds)
keeps this stress scenario bounded; it does not simulate normal user retention
or replace the separate statistics and multi-day retention tests.

Every sample includes state inserts, retained state rows, attribute growth,
database size and WAL size. Queue depth and file-size peaks are also sampled
every 250 ms, and the queue must drain at the end. The scenario allows at most
eight state inserts per measurement iteration, plus two availability changes
per enabled entity and reconnect. Attribute growth is limited to two rows;
database/WAL peaks to 8/16 MiB and queue depth to 256 items. These are regression
budgets for one synthetic wallbox, not limits imposed on a user's database.
`scripts/recorder_soak_checks.py` owns these checks and rejects incomplete
Recorder measurements even when the serial test itself succeeds.

The continuous run checkpoints its measurements after each sample. Reports
include completion status, the last test stage and failure categories; a
test failure or timeout retains the available samples. CI uploads this report
even when the test fails. With `--json-output`, checkpoints are written directly
to the artifact path so they also survive interruption of the runner itself.
A partial report cannot pass validation.

## AppArmor enforcement

App 0.5.2b1 adds a custom profile for the Supervisor app. Syntax validation and
runtime enforcement are separate checks. A parser run must accept the pinned
ABI and rules without warnings; it does not prove that the service can operate
under those rules.

The enforcement check is designed for a **disposable Linux CI runner** with
Docker, kernel AppArmor support and passwordless `sudo -n`:

```bash
.venv/bin/python scripts/check_service_apparmor.py --image IMAGE
```

Replace `IMAGE` with the locally built service image. This check loads a uniquely
named profile through `sudo -n`, runs the actual service image under enforcement,
and removes its test containers and profile afterward. It tests simulated USB,
RTU-over-TCP and Modbus TCP connections, heartbeat, permitted token/state writes,
restart and shutdown. Negative probes must reject writes outside the service's
state files, modification of options, protected-file reads and shell execution.
A successful startup alone cannot pass the enforcement check.

The native AMD64 and ARM64 CI jobs run this check on every `beta`/`main` push and
pull request. Release builds require it before pushing an app image. Missing
AppArmor support or a failed enforcement check stops the job. On development
hosts without Docker or permission to load profiles, `--compile-only` verifies
syntax and explicitly reports that enforcement was not tested. Simulated
transports do not establish behavior on physical adapters or wallbox firmware.

## Published release verification

Local HACS builds use an independent temporary staging directory per invocation.
The final ZIP appears only after the build completes; a failed build preserves
an existing archive. Concurrent builds, including those sharing an output path,
are covered by regression tests.

```bash
.venv/bin/python scripts/release_check.py --ref v0.5.1 --published
```

Use the intended release tag or approved commit. This read-only check requires
authenticated `gh`. It resolves annotated tags, checks stable-release metadata
and the exact committed notes, downloads the four expected assets, verifies
GitHub digests, SHA256SUMS, build metadata and the SBOM package identity, then
compares every ZIP member with the approved Git tree. It rejects duplicate,
extra, changed or non-regular files. The HACS layout remains flat, with LICENSE
and NOTICE included. `--archive PATH` also verifies a local package. The release
workflow runs the published check after uploading the assets, in addition to
its existing SPDX schema and attestation verification before publication.

The downloaded SBOM must also list exactly the manifest's dependencies,
including extras and version constraints, with unique package IDs and matching
dependency relationships. Recomputing checksums cannot hide missing, additional
or changed dependency declarations from this check.

Publication first uploads a draft, downloads and validates all four assets,
and only then makes it public. A repeated run verifies existing files without
replacing them; any difference stops publication. Metadata and the SBOM use
the source commit timestamp (`source_date`) and a deterministic document
identifier, so the same source and ZIP produce identical verification files.
An incomplete draft remains private for inspection. Published files are never
repaired by overwriting them; corrections require a new version.

For an integration-only release, also verify that the app publication plan
reuses the existing service: its version, image digest, original source and
catalog commits must remain unchanged. A service update instead needs its own
new version and image/catalog verification. See [Service publication](service-releasing.md)
for the relevant-input comparison and independent version sources.

## Stable and preview compatibility

Superseded PR validation runs are cancelled. Main, scheduled and manual runs
keep their evidence; release publication is serialized per tag and never
cancelled by another publisher. Jobs have explicit time limits. Python jobs
cache pip downloads by dependency lock and integration manifest, while still
resolving and verifying the current latest stable HA on every run.

PR and release validation each have exactly two HA targets: the fixed minimum
from `project-versions.json` and the automatically resolved latest stable release.
Both run the full test suite; latest stable also runs strict typing. After tool
and runtime dependency installation, CI verifies the installed HA version.
The latest target is compared with Core's published latest stable release, so
an older Python-compatible fallback cannot pass unnoticed. No current or beta
version pin needs routine updates.

The separate `next-ha` early-warning job runs only weekly or manually. It tracks
prereleases independently and makes maintenance failures visible. It is not a
required PR/release check. The resource soak uses latest stable as well; none
of these checks rewrites pins or opens dependency PRs.

`scripts/check_deprecations.py` checks owned Python calls for a focused list
of obsolete APIs and rejects blanket suppression of deprecation warnings.
It does not flag historical documentation, string test vectors or HA's own
UTC helper. Third-party warnings remain visible and must be assessed separately.

## Public privacy audit

```bash
python3 scripts/audit_github_public.py --hacs-pr 11749
```

The read-only audit scans public releases and their uploaded assets, issue and
PR bodies, reviews, discussion comments, review comments and the specified HACS
PR. It follows pagination and reads ZIP members in memory with size and path
limits. It never edits GitHub or extracts an archive. Reports contain numeric
locations and categories only; matched credentials or identifying text are
never printed. The weekly/manual CI job fails on findings or incomplete reads.

Patterns cover private keys, common GitHub/bearer tokens, literal credentials,
private IP addresses, user home paths and serial adapter paths. Placeholder
values and reserved documentation IP addresses are allowed. Findings require
human assessment: examples can trigger a match. This is a heuristic check,
not a guarantee of anonymity or a replacement for secret scanning. Deleted
content, source archives generated by GitHub, git history, log artifacts,
linked attachments and text within images are outside its scope.

## Serial fault injection

`tests/test_serial_faults.py` sends synthetic RTU frames through pseudo-terminals
and the real pymodbus client. It covers fragmented frames, a foreign slave,
invalid CRCs, truncated frames, delayed replies, wrong functions and mismatched
write acknowledgements. Failed reads retain their retry budget and release the
bus for a heartbeat. An uncertain write is never retried automatically and
retains its register cooldown. Successful writes must acknowledge the requested
function and address, plus the value (0x06) or register count (0x10).

RTU provides no transaction identifier and read replies do not include the
requested register address. A delayed read reply with the same slave, function
and word count cannot be distinguished from a fresh reply solely by its bytes.
These tests prove rejection of distinguishable faults; they do not remove that
protocol limitation or replace tests on a physical RS-485 installation.
