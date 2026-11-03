# Maintenance validation

Run the normal suite with `.venv/bin/python scripts/check_validate.py`.
The minimum HA environment uses `--skip-typing`; latest stable HA
also runs strict typing. These checks do not access a physical wallbox.

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
does not replace the separate SQLite Recorder retention tests or hardware tests.

The continuous run checkpoints its measurements after each sample. Reports
include completion status, the last test stage and failure categories; a
test failure or timeout retains the available samples. CI uploads this report
even when the test fails. With `--json-output`, checkpoints are written directly
to the artifact path so they also survive interruption of the runner itself.
A partial report cannot pass validation.

## Published release verification

Local HACS builds use an independent temporary staging directory per invocation.
The final ZIP appears only after the build completes; a failed build preserves
an existing archive. Concurrent builds, including those sharing an output path,
are covered by regression tests.

```bash
.venv/bin/python scripts/release_check.py --ref v0.2.3 --published
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
