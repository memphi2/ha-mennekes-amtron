# Current Legal and Provenance Audit Snapshot

Development on 2026-11-05 also adds outgoing connections through transparent
RTU-over-TCP adapters and translating Modbus TCP-to-RTU gateways. This changes
the network trust boundary without expanding the documented RTU wallbox/model
scope. Gateway Modbus traffic is unauthenticated and unencrypted; the service's
authenticated HTTP API remains a separate connection. See the current
[security model](../../SECURITY.md), [privacy policy](../../PRIVACY.md) and
[LAN guide](../network-gateway.md). The historical validation counts below do not
cover these changes or establish hardware verification of LAN adapters.

The v0.3.0 evidence below is historical. Development on 2026-11-04 adds the
independent [Modbus service](../modbus-service.md), including an authenticated
HTTP listener, stored credentials and an `aiohttp` dependency. Its current
boundaries are documented in [SECURITY](../../SECURITY.md),
[PRIVACY](../../PRIVACY.md) and [legal](../legal.md). The older no-listener
statement below applies to direct serial mode. Service image publication now has its own native-container and provenance
gates. Physical wallbox and Supervisor acceptance remain separate checks.

Audit date: 2026-11-02

This is a technical repository audit for v0.3.0, following the published
v0.2.3 at `e71e538bcdad64209714a99123224af8f9003ab5`. It is not a legal opinion,
a trademark or patent clearance, or a certification of the integration.

## Scope and evidence

The repository is public. The previous audit remains available in
[Git history](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.2.3/docs/audits/current-legal-provenance.md)
as a dated record of v0.2.3.

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

Current audit validation on 2026-11-02 uses Python 3.14 and pymodbus 3.13.1.
The resolved dependency is checked against each Home Assistant installation,
including its package constraints:

| HA version | Tests passed | Skipped | Statement/branch coverage |
|---|---:|---:|---:|
| 2026.9.0 | 627 | 2 | 99.46% |
| 2026.10.4 | 627 | 2 | 99.46% |

The two skips concern Marketplace APIs absent in the older HA versions.
Strict typing passed for all 60 source/tooling modules against latest stable
HA; minimum HA runs the runtime suite without the typing gate.
The local HACS build contains 55 files, including byte-identical LICENSE and
NOTICE copies. These results record the pre-release audit; publication and
verification of downloaded release assets are separate release gates.

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
when unchanged polls are suppressed. Additional real-serial failure tests
verify that lost replies do not cause automatic write retransmissions and
that cancellation stops a probe and releases its port. Rejected setpoints
preserve the previous accepted current, phase-change pauses remember the
actual current, and HA refresh callbacks see that value immediately. A real
HA automation test checks replacement of an in-flight PV adjustment by the
latest power budget. The integration remains read-only by default and exposes
no network listener or cloud API.

The settings tool migrates required checks to minimum and latest stable HA
through GitHub's dedicated endpoint, preserving review rules, administrator
enforcement and existing App IDs. Only the replaced current/preview contexts
are removed; the mandatory `codex-security-review` gate remains. The local
policy is applied and verified separately during release preparation.
The owner confirmed automatic Codex security review for all PRs on every push.

The security gate has 48 regression cases, including edited review requests,
timestamp ordering and authorization. Its publisher can successfully
deliver a blocking `pending` status without treating normal waiting as a job
failure. Read-only checks still fail until review approval. The gate uses
authenticated bot evidence and executes only trusted default-branch code; see
[repository settings](../repository-settings.md#required-codex-security-review)
for format dependencies, reporting thresholds and the initial rollout.

Routine Dependabot PRs remain disabled; security alerts and security updates
remain part of the repository policy. This reduces routine work, but it is
not a promise of maintenance-free compatibility with future HA releases.
The min/latest stable matrix runs for PRs and releases. The next-HA preview
is tested only weekly or manually, making maintenance regressions visible
without creating dependency-update PRs. A deprecated-API guard checks owned
code while leaving third-party warnings visible.

[Recorder](../recorder.md) documents the 30-second measurement default,
complete lean exclusion profile, measured state-row savings and the limits
of the synthetic replay. The integration does not change global Recorder
configuration, delete historical data or impose a database size cap.
The new retention test replays six daily charging windows and four real
purge/repack cycles in both full and lean profiles. It checks stable detailed
history counts, retained hourly statistics, actual SQLite VACUUM completion
and database integrity. Disk-monitoring guidance covers the database mount
and the temporary free space needed for maintenance.

The bounded diagnostics history retains at most 40 events in RAM, outside
entity data and Recorder state. Thirty measured lifecycle and serial cycles
showed no growing task, timer or descriptor count and no retained clients or
listeners after unload. Fault injection checks mismatched RTU responses and
preserves the uncertain-write cooldown. Public privacy scanning inspected 156
published text/asset surfaces before this release, with no heuristic findings
other than documented placeholders; post-publication checks are separate.

## Limits

No public clone/similarity search, jurisdiction-specific clearance or legal
opinion was performed. Static provenance checks cannot establish ownership
of every contribution. The owner reported successful use of the released
integration on an Amtron 4You 310 11 C2 on 2026-11-03; see the
[hardware-test scope](../compatibility.md). This does not validate later
development changes or other models. Automated serial transport checks use
a virtual RTU bus. Synthetic
Recorder replay, statistics and accelerated retention tests are not a
wall-clock or disk-full soak test. Testing the minimum and latest stable HA
does not guarantee compatibility with every intermediate or future release.
