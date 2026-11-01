# Current Legal and Provenance Audit Snapshot

Audit date: 2026-11-01

This is a technical repository audit of the local changes following v0.2.2,
based on `7bb602d39632092c238cdc12c70a72a3d43f79d1`. It is not a legal opinion,
a trademark or patent clearance, or a certification of the integration.

## Scope and evidence

The repository is public and v0.2.2 has been released. The previous document
still described the initial unpublished 0.1 work; its later edits had also
changed some dependency facts without updating the audit date. That document
remains available in [Git history](https://github.com/memphi2/ha-mennekes-amtron/blob/7bb602d39632092c238cdc12c70a72a3d43f79d1/docs/audits/current-legal-provenance.md)
as historical context, not evidence of the current state.

The current review covers tracked integration code, tests, repository tooling,
user documentation, dependencies, generic artwork and release packaging.
Local checks are reproducible with:

```bash
.venv/bin/python scripts/check_validate.py
.venv/bin/python scripts/apply_repo_settings.py --check
```

The first command runs repository, provenance, quality, register-map,
dependency, Ruff, test/coverage and strict-typing gates. The second reads
GitHub required-check settings and reports drift without changing them.
It is separate from code validation and requires authenticated `gh` access.

Local validation on 2026-11-01 used Python 3.14 and pymodbus 3.13.1:

| HA version | Tests passed | Skipped | Statement/branch coverage |
|---|---:|---:|---:|
| 2026.9.0 | 480 | 2 | 99.43% |
| 2026.10.0 | 480 | 2 | 99.43% |
| 2026.11.0b1 | 482 | 0 | 99.43% |

The two skips concern Marketplace APIs absent in the older HA versions.
Strict typing passed for all 53 source/tooling modules against current and
preview HA; minimum HA runs the runtime suite without the typing gate.
The local HACS build contains 54 files, including byte-identical LICENSE and
NOTICE copies. It is a development build; no new release has been published.

## License, attribution and artwork

- `LICENSE` matches the [Apache License 2.0 text](https://www.apache.org/licenses/LICENSE-2.0.txt)
  after normalizing whitespace. `NOTICE` retains attribution, trademark and
  non-affiliation notices. The HACS archive includes both files.
- The manifest requests `pymodbus[serial]>=3.13.1`. Home Assistant owns the
  resolved version. [pymodbus 3.13.1](https://pypi.org/project/pymodbus/3.13.1/)
  declares BSD-3-Clause; neither it nor pyserial is vendored in the archive.
- Brand assets are the generic project-owned plug-and-bolt artwork described
  in [legal.md](../legal.md). Both images retain SHA-256
  `1ec09c77821817d235eb180da6ca006ab305074ffdcda0e2a44b7b85a4897c63`.
- The display name identifies compatible hardware. README, NOTICE and the
  legal documentation retain the independent-project/non-affiliation notice.
  No vendor logo or certification badge is shipped.

## Protocol provenance and repository hygiene

The documented protocol source remains MENNEKES' Modbus RTU specification,
revision 2.5, dated 2025-07-11, register layout v01.03. The repository contains
its own register tables and implementation, not the manufacturer's PDF,
firmware or configuration tool. The source reference and register contract
are documented in [modbus-registers.md](../modbus-registers.md).

The repository/provenance gates pass their tracked-file checks for prohibited
payloads, vendor-document fingerprints, private-key patterns, identifying
serial paths, runtime artifacts, metadata and artwork hashes. These are
bounded checks, not proof that every possible secret or copyright overlap
has been excluded. The old document's detailed overlap measurements were
not rerun against the complete vendor document during this audit and are
not presented as current evidence.

## Security, maintenance and resource use

Local regression coverage includes HA action permissions, exclusive serial
ownership during reconfigure, conservative cooldown after uncertain writes,
phase-switch preflight, optional blueprint failures and repair notifications
when unchanged polls are suppressed. The integration remains read-only by
default and exposes no network listener or cloud API.

The settings tool now merges required checks through GitHub's dedicated
endpoint, preserving review rules, administrator enforcement and existing
App IDs. API errors fail the command. The read-only check on 2026-11-01 found
`validate (preview-ha)` missing from live required checks; this local audit
has not applied GitHub settings. Passing local tests does not fix that drift.

Routine Dependabot PRs remain disabled; security alerts and security updates
remain part of the repository policy. This reduces routine work, but it is
not a promise of maintenance-free compatibility with future HA releases.

[Recorder](../recorder.md) documents the 30-second measurement default,
complete lean exclusion profile, measured state-row savings and the limits
of the synthetic replay. The integration does not change global Recorder
configuration, delete historical data or impose a database size cap.

## Limits

No public clone/similarity search, jurisdiction-specific clearance or legal
opinion was performed. Static provenance checks cannot establish ownership
of every contribution. Hardware behavior has not been verified on a physical
wallbox; serial transport is tested through a virtual RTU bus. Synthetic
Recorder replay and hourly-statistics tests are not a real-time retention or
disk-full soak test. November beta compatibility is evidence for that tested
beta, not a guarantee about a future stable release.
