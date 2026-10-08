# Support Policy

## Supported Release Line

- `0.1.x`: active

## Compatibility Baseline

- Minimum Home Assistant: `2026.9.0`
- Validated Home Assistant: `2026.9.x` and `2026.10.x`
- Early warning: every push is also validated against the newest Home
  Assistant pre-release in a job that is allowed to fail, so a breaking change
  in Core is noticed before it ships rather than after. The version is
  resolved, not pinned, so the job never tests a superseded beta
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

## Validated Hardware

- AMTRON 4You 310 11 C2, article `1313201205`, 11 kW, fixed cable, 16 A.

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
`async_update_reload_and_abort` with `data_updates`), the progress step with a
`progress_task` that the bus search uses, and typed `runtime_data` on the
config entry. Lowering the minimum means adding that version to the matrix
first.

## Maintenance Scope

Security and compatibility fixes target the current minor release line only,
unless a release note states otherwise.
