# Support Policy

## Supported Release Line

- `0.3.x`: active

## Compatibility Baseline

- Minimum Home Assistant: `2026.9.0`
- Required PR and release validation: the fixed minimum and the latest stable
  Home Assistant. Latest is resolved automatically, without changing a version
  pin or opening an update PR. CI verifies the installed version after resolving
  dependencies; an older Python-compatible fallback or a beta cannot pass as
  latest stable.
- Early warning: the newest available prerelease is tested only on the weekly
  Tuesday 05:43 UTC maintenance run or manual dispatch. Failures make those
  maintenance runs red, but do not add a required PR/release matrix target.
  Enable GitHub Actions notifications to receive compatibility warnings.
- The same two-target matrix also runs weekly and manually. These runs create
  no update PRs.
- Python: `3.14`
- Modbus register layout: `v01.03` (validated), `v01.00`–`v01.02` supported
  with capability gating and untested
- pymodbus: minimum `3.13.1`, declared as `pymodbus[serial]>=3.13.1`

Home Assistant Core pins pymodbus exactly for the whole instance, and that pin
moves between releases: every release in the supported range resolves
`3.13.1`. An exact pin here would fight Core the moment that changes, so
`manifest.json` declares a minimum and lets Home Assistant own the resolved
version. The floor is the oldest version any supported Home Assistant
resolves to. `scripts/check_pymodbus_pin.py` enforces that the declared
minimum stays a minimum and stays at or below what the installed Home
Assistant resolves to.

Validation installs the selected Home Assistant first, then its tooling and
`python scripts/check_pymodbus_pin.py --install`. This resolves pymodbus using
that HA version's pin and package constraints. The gate checks the **installed**
version too, so a future Core update cannot pass while tests still use the
older development pin. The development requirements retain their baseline pin
for local bootstrapping and dependency alerts.

Core also ships `tmodbus` and `modbus-connection` next to pymodbus for its own
`modbus` integration, so Core may be migrating away from pymodbus. That does
not change anything here: if Core drops pymodbus, Home Assistant installs it
for this integration alone.


## Schema Validation

Home Assistant replaced voluptuous with `probatio` in `2026.9` and, from
`2026.10`, types its own config-flow and action signatures against it. It
still aliases the name `voluptuous` onto `probatio` for compatibility, so the
old import keeps working -- but only because Home Assistant is imported
first, and `mypy --strict` cannot see that the two names are the same class.
This integration therefore imports `probatio` directly, as Core itself does,
and `scripts/check_repo.py` fails the build if a voluptuous import returns.

Because Core's annotations differ between the two ends of the supported
range, one source file cannot satisfy `mypy --strict` against both. The
minimum matrix entry runs the full test suite and proves the integration
works there; the current entry additionally runs the typing gate. Neither
entry skips a runtime check.

## Distribution validation

The integration uses one release ZIP for both HACS and the built-in Marketplace.
The manifest declares the `energy.ev_charging` category. Installation guidance
covers the [Marketplace and older HACS installations](docs/installation.md).

On HA 2026.11+, `tests/test_marketplace.py` runs the real Marketplace installer
against the built release archive in a disposable HA configuration. Downloads
come from local fixtures and the catalog entry is synthetic. The test checks
first-install discovery, update restart requests, invalid-package rollback
and preservation of a separate TCP integration. On older HA versions it is
skipped because the Marketplace does not exist; release packaging and runtime
tests still run there. This does not claim catalog acceptance or a browser test.

## Reference Hardware

- AMTRON 4You 310 11 C2, article `1313201205`, 11 kW, fixed cable, 16 A.

Validation uses simulated registers, a virtual serial RTU link and real
Home Assistant/SQLite. Physical wallbox validation is still outstanding;
the reference model is not a claim of a completed hardware test.

Other AMTRON models covered by the same Modbus specification (4You 300,
Compact 2.0s, Start 2.0s) are expected to work through capability gating but
are **untested**. Issues from those models are welcome and will be labelled as
unvalidated hardware.

## Why the minimum is where it is

The minimum is the oldest Home Assistant the CI matrix really runs, not the
oldest one the code might import successfully. Older versions are not claimed
because they are not tested.

The features that set the floor are the reconfigure-flow helpers
(`_get_reconfigure_entry`, `_abort_if_unique_id_mismatch`,
`async_update_and_abort` with `data_updates`), the progress step with a
`progress_task` that the bus search uses, and typed `runtime_data` on the
config entry. Lowering the minimum means adding that version to the matrix
first.

## Maintenance Scope

Security and compatibility fixes target the current minor release line only,
unless a release note states otherwise.
