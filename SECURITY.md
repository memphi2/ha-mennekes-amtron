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
- **Single master.** RS-485 allows exactly one master. Running this
  integration in master mode on a bus that already has one produces
  unpredictable behaviour, not just failed reads. The read-only control mode
  exists for that case.
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
