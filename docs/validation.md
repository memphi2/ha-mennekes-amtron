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
