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

The wallbox uses a local RS-485 bus, reached through USB or a LAN-to-RS-485
adapter. Direct mode opens an outbound connection and has no inbound network
listener. The independent Modbus service adds an authenticated HTTP API and a
bearer credential; neither mode requires a cloud component.

What matters for security here is different from a cloud integration:

- **Physical bus access.** Anyone with access to the RS-485 pair can read and
  write the wallbox regardless of Home Assistant. Treat the bus as a trusted,
  physically protected segment.
- **Single master.** Modbus RTU allows exactly one master. This integration
  transmits read requests in both control modes. Read-only disables register
  writes and heartbeat, but does not make sharing with another master safe.
  Setup, discovery and the smoke test also require exclusive bus access.
- **LAN adapter access.** Transparent RTU over TCP and translating Modbus TCP
  gateways add an unauthenticated, unencrypted Modbus network connection. Anyone
  who can reach that port may be able to send charging commands independently of
  HA permissions. Use a trusted private network, restrict gateway access to the
  selected HA/service host, and never expose it to the Internet. A service token
  protects the separate HTTP API, not the gateway's Modbus port. Multiple TCP
  clients do not remove the single-master requirement on the RS-485 bus.
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
  Supervisor administration permission. App tokens are generated once and
  stored privately. The app has no manual-token mode or fallback; standalone
  services still require an explicit token.
- **Independent lifetime.** HA disconnecting from the service does not stop
  charging or its heartbeat. The last state is retained indefinitely by explicit
  design. USB, LAN/gateway, host and service failures still require the wallbox's
  configured fallback. A direct HA connection, whether USB or LAN, stops its
  heartbeat during a Core restart.
- **Private state.** The service saves bounded intent with atomic owner-only
  files. Protect HA configuration, service options and backups, which hold the
  credential. Diagnostics do not include service URLs, credentials or gateway
  hostnames/IP addresses.

## Service AppArmor profile

The beta development branch adds a custom Supervisor-app profile; released
0.5.1 does not include it. Supervisor loads the packaged `apparmor.txt` for the
installed app without disabling protection mode. The allowlist permits the
Python runtime, USB/LAN communication and the service's private token and intent
files. Options are read-only; there is no general filesystem-write or shell
execution permission. This does not add authentication or encryption to Modbus
or restrict TCP destinations to a particular gateway.

The profile is separate from the OCI image. Standalone Docker deployments must
load and select a suitable profile explicitly. Direct HA connections use the
security policy of Home Assistant and its host, not this service profile.
Runtime tests must prove both permitted service operation and denied unrelated
access; see [AppArmor validation](docs/validation.md#apparmor-enforcement).
For denials, retain protection and collect Supervisor/kernel logs identifying
the blocked operation, with private values removed.

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
