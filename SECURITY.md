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

The wallbox uses a local serial bus. Direct mode has no inbound network
listener. The independent Modbus service adds an authenticated HTTP API and
a bearer credential; neither mode requires a cloud component.

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
  boundary for HA accounts. A holder of the independent service token can
  also send named commands directly; protect it as a charging-control credential.
- **Hardware limits are not ours to enforce.** The §14a downgrade, the
  installation current and the EVSE maximum are enforced inside the wallbox.
  The integration cannot raise them and must never be described as if it
  could.

- **Service boundary.** The API requires a token of at least 32 characters,
  uses constant-time comparison, bounds request bodies and supports only named
  commands and approved diagnostic reads. It exposes no arbitrary write tunnel
  or shutdown action. `/health` reveals process liveness only. Redirects are not
  followed by the client. Use a trusted private network or an HTTPS reverse proxy;
  the service does not terminate TLS itself. Never publish its port to the Internet.
- **App discovery.** Supervisor transfers the service credentials locally using
  its authenticated discovery API. The integration validates the app slug and
  restricts discovery connections to that app's internal hostname and port 8765.
  Credentials never travel in a broadcast. App discovery needs no additional
  Supervisor administration permission. Blank app tokens are generated once and
  stored privately; standalone services still require an explicit token.
- **Independent lifetime.** HA disconnecting does not stop charging or the
  heartbeat. The last state is retained indefinitely by explicit design. USB,
  host and service failures still require the wallbox's configured fallback.
- **Private state.** The service saves bounded intent with atomic owner-only
  files. Protect HA configuration, service options and backups, which hold the
  credential. Diagnostics do not include service URLs or credentials.

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
