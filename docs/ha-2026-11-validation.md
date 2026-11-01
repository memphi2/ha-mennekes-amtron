# Home Assistant 2026.11 preparation and repository audit

Snapshot: 2026-11-01. Local preparation based on v0.2.1, commit
`7fdec943cd3ff966f36f34405a20ad07688129e6`. Integration version remains
0.2.1; the changes are documented under **Unreleased**.

## Changes and regression evidence

| Point | Result and evidence |
|---|---|
| Reconfiguration | A loaded entry uses its update listener for one reload; unchanged data does not reload. An unloaded entry explicitly reloads so setup can recover. Real Home Assistant tests cover all four combinations. |
| November compatibility | Fixed 2026.11.0b1 validation in PR and release matrices, with strict typing. Platform enum imports retain September runtime compatibility. The declared required checks include the preview job. |
| Recorder and long-term statistics | Real SQLite compilation verifies hourly energy increments, power averages, metadata and three exclusion profiles. See [Recorder guidance](recorder.md). |
| Bus ownership | Setup help, translations and documentation now explain that read-only still transmits requests. Modbus RTU requires a single master in both control modes. Hardware claims identify simulated validation. |
| Repair lifecycle | Deleting a config entry removes only its own repair issues. Reloading preserves them, and issues from other entries or domains remain. |
| Serial probe cleanup | Failed or cancelled connection attempts close the transport before another search candidate is tried. Regression tests reproduce both paths. |
| Marketplace metadata | The manifest declares `energy.ev_charging`; the same ZIP and `hacs.json` serve the Marketplace and older HACS installations. |
| Installation guidance | The DHE Connect structure is adapted to AMTRON: Marketplace and HACS instructions, existing entries, updates and removal. The pending catalog submission and the separate 0.1.0 domain migration remain explicit. |
| Marketplace installer | Offline tests run the real November installer with our built ZIP, including first-install discovery, update restart requests, invalid-package rollback and preservation of a separate TCP integration. |

The reconfiguration change follows the
[update-listener deprecation](https://contributors.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/):
an entry with a reload listener must not also request a reload from the flow
helper. The announced error starts in 2026.12; that release has not been tested.

The [2026.11 entry return-value change](https://contributors.home-assistant.io/blog/2026/10/23/config-entry-methods-return-none/)
continues to support boolean returns for custom integrations. Those returns
remain compatible with the supported September baseline.

## Validation

All runs used Python 3.14.4 and pymodbus 3.13.1. Each environment passed
`python -m pip check` and `python scripts/check_validate.py`; the minimum
environment used `--skip-typing`, matching CI.

| Home Assistant | Tests | Coverage | Strict typing |
|---|---:|---:|---|
| 2026.9.0 | 413 passed, 2 skipped | 99.42% | Skipped: older Core annotations |
| 2026.10.0 | 413 passed, 2 skipped | 99.42% | Passed, 53 source files |
| 2026.11.0b1 | 415 passed | 99.42% | Passed, 53 source files |

The two skips on September/October are the Marketplace installer tests:
that component first ships in November. They are required by the fixed
November CI job. The tests use a disposable HA configuration, local download
fixtures and a synthetic catalog entry. GitHub API access is blocked in the
test. This verifies the install backend, not browser interaction, account
authorization or actual catalog acceptance.

The gate includes Ruff, repository consistency, register-map validation,
quality-scale checks, dependency constraints and the legal/provenance audit.
Tests cover config/options flows, lifecycle, control/recovery limits,
heartbeat and serial locking, real RTU transport over virtual serial ports,
entities, diagnostics redaction, Recorder, translations and release packaging.

An additional package check built the same source twice and confirmed
byte-identical output: one flat HACS installation ZIP containing 54 files.
Checksums and build metadata matched; the generated SPDX 2.3 document passed
`spdx-tools` validation. Local documentation links and diff whitespace were
also checked.

Each test run reports five upstream deprecation warnings: Home Assistant's
aiohttp application inheritance and `backoff` use of an asyncio API scheduled
for removal in Python 3.16. These warnings were not suppressed. The tested
reconfiguration paths no longer emit the update-listener warning.

## Database behavior and remaining checks

The existing 30-second measurement default limits state-history traffic.
It does not change the fixed statistics cadence. With valid values, the
default 17 statistics series generate 408 hourly rows per day. The documented
exclusion profiles leave seven or two series; the latter retains total energy
and power and generates 48 hourly rows per day. Excluded entities remain live.

These profiles require an explicit change to the user's Recorder filters.
Existing long-term statistics are not deleted by the integration or by
`purge_keep_days`. The tests prove statistics correctness and exclusion
behavior, not a fixed storage bound or a physical charging-day soak test.

2026.11.0b1 is the tested preview. After the stable release, update the current
target, workflow matrices and support policy together and repeat validation.
Physical wallbox validation remains outstanding. GitHub-hosted HACS,
Hassfest and CodeQL jobs were not run as part of this local audit.

No push, tag, publication, live Home Assistant change or database deletion was
performed. The required-check declaration was updated locally; GitHub branch
protection was not changed.
