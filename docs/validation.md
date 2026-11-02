# Maintenance validation

Run the normal suite with `.venv/bin/python scripts/check_validate.py`.
The minimum HA environment uses `--skip-typing`; current and preview HA
also run strict typing. These checks do not access a physical wallbox.

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
open clients. A successful subprocess without a valid report cannot pass.
CI runs 30 cycles, while the ordinary suite uses three for a quicker regression
check. The serial simulator also closes its relay threads and descriptors.
This bounded stress test does not replace physical-wallbox validation, a
wall-clock soak lasting days, or the separate Recorder retention tests.

## Published release verification

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
