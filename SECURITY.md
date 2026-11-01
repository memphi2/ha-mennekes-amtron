# Security Policy

## Supported Versions

Security fixes target the current release line. See [SUPPORT.md](SUPPORT.md).

## Reporting a Vulnerability

Report suspected vulnerabilities through a private GitHub security advisory on
this repository. Please do not open a public issue for an unfixed
vulnerability.

Include the integration version, the Home Assistant version, the Modbus layout
version of your wallbox and the smallest reproduction you have.

## Threat Model

This integration talks to a wallbox over a local serial bus. It has no cloud
component, no inbound network listener and no credentials.

What matters for security here is different from a cloud integration:

- **Physical bus access.** Anyone with access to the RS-485 pair can read and
  write the wallbox regardless of Home Assistant. Treat the bus as a trusted,
  physically protected segment.
- **Single master.** Modbus RTU allows exactly one master. This integration
  transmits read requests in both control modes. Read-only disables register
  writes and heartbeat, but does not make sharing with another master safe.
  Setup, discovery and the smoke test also require exclusive bus access.
- **Write authority.** In master mode the integration can release charging,
  lock the wallbox and restart it. Home Assistant user permissions are the
  only thing between a Home Assistant account and those actions.
- **Hardware limits are not ours to enforce.** The §14a downgrade, the
  installation current and the EVSE maximum are enforced inside the wallbox.
  The integration cannot raise them and must never be described as if it
  could.

## Out of Scope

- Firmware vulnerabilities in the wallbox itself. Report those to MENNEKES.
- Attacks that require physical access to the RS-485 wiring.

## Required pull-request review

Every PR into `main` requires a completed Codex Security Review for its current
head commit, in addition to the normal code review and CI. The required
`codex-security-review` status accepts only authenticated Codex bot evidence
and an explicit clean security-review receipt. Missing, stale, failed or
inconsistent results block merging. Findings require remediation and a new
review; a normal code-review receipt cannot substitute for a security review.

Automatic security reviews are configured for all PRs on every push in Codex.
To retry manually, post exactly `@codex security review` in the PR. Reporting
thresholds are configured in Codex; a clean receipt means no issues were
reported under that policy, not proof that the code has no vulnerabilities.
See [repository settings](docs/repository-settings.md#required-codex-security-review)
for the enforcement workflow and initial rollout.
