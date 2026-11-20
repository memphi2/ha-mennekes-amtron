# Repository Guidelines

## Project Structure

`custom_components/mennekes_amtron_rtu/` contains the Home Assistant integration. Its `device/` package provides the shared Modbus driver, standalone service, and app HTML/CSS/JavaScript; keep this package independent of Home Assistant. `modbus_service/` holds Supervisor app metadata and AppArmor policy. Tests live in `tests/`, validation and packaging tools in `scripts/`, user guides in `docs/`, and dashboard/automation examples in `examples/`.

## Development Commands

Use Python 3.14 in an isolated environment. Install Home Assistant separately and development dependencies from `requirements-dev.txt`; use `requirements-dev-min-ha.txt` for the minimum HA environment.

- `python scripts/check_validate.py`: run repository, legal, register, lint, deprecation, coverage, and typing gates.
- `python scripts/check_validate.py --skip-typing`: validate the minimum supported HA release; strict typing runs against latest stable HA.
- `python -m pytest tests/test_shared_guest_limits.py`: run focused regressions.
- `node --test tests/*.test.cjs`: test app JavaScript.
- `python -m ruff check .`: lint Python.
- `python scripts/build_hacs_release.py`: build the local HACS ZIP in `.release/`.

For standalone setup and execution, follow `docs/service-standalone.md`.

## Coding Style

Use four-space Python indentation, type annotations, `snake_case` functions/modules, `PascalCase` classes, and uppercase constants. Follow Ruff rules in `pyproject.toml`; avoid unrelated formatting changes. Keep blocking filesystem work off the event loop. Write visible log lines as `Area: what (reason)` with an area from `tests/test_log_format.py`, and report a failure that recurs on every poll or retry through `device/notice.py` instead of a private flag. Update translated strings and relevant examples when changing controls.

## Testing

Use pytest, simulated Modbus transports, HTTP fixtures, and controlled clocks. Name Python tests `test_*.py` and JavaScript tests `*.test.cjs`. Reproduce failures before fixing them; cover cancellation, reconnects, stale commands, and persistence failures. Required statement/branch coverage is at least 99% overall, strictly above 95% per production module, and 100% for designated configuration-flow modules. Validate minimum and latest stable HA. Run focused tests during fixes and complete gates before publication.

## Commits and Pull Requests

Use short imperative subjects, such as “Reject backwards session evidence.” Keep one logical fix per commit. PRs should explain the trigger, resulting behavior, validation, breaking changes, and related issues; include screenshots for UI changes. Review automatic code/security findings and pass required CI before merging. Follow `docs/service-releasing.md` for publication.

## Compatibility and Safety

1.0.0 starts without migrations or compatibility fallbacks for earlier versions; keep none for them and document breaking changes. Preserve identity checks, electrical interlocks, and write intervals. Maintain one Modbus owner. Avoid per-heartbeat Recorder updates and unbounded histories. Keep credentials out of logs and commits. Local edits do not authorize hardware access or publication.
