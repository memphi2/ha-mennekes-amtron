# Privacy

## What this integration collects

There is no cloud service or telemetry. Direct mode speaks Modbus through a local
USB/serial adapter or an outgoing TCP connection to a configured LAN-to-RS-485
gateway. Service mode exchanges wallbox readings and control commands
with the configured Modbus service URL. Keep that service on a trusted private
network, or protect its HTTP connection with HTTPS. The gateway Modbus connection
itself is unencrypted and unauthenticated; restrict it to a trusted private LAN.

## What is stored in Home Assistant

- The serial port path, bus parameters and Modbus device address, in the
  config entry for direct USB mode. Direct LAN mode stores the gateway host,
  TCP port, connection protocol and device address. Service mode stores the service URL
  and access token in the config entry; protect HA backups accordingly.
- The wallbox serial number, if the firmware exposes it, as the config entry's
  unique id and in the device registry.
- Register values, as entity states and in Home Assistant's recorder database
  like any other entity.

## What the independent service stores

Standalone service configuration contains its USB or LAN connection settings and
access token. LAN settings include the gateway host, TCP port and protocol.
The Supervisor app exclusively manages its own token in an owner-only
`service-token` file, reused across restarts. Supervisor stores the app's discovery
address and token and transfers them locally to Home Assistant; no network
broadcast or cloud discovery is involved. Protect Supervisor backups as well. Its
small `intent.json` file stores the wallbox identity, pause/release intent and
resume current. It is replaced only when intent changes, with no growing history.
Heartbeats and polls are not persisted. Protect service data and backups.

## What diagnostics contain

The diagnostics download is built from an allowlist. It deliberately omits:

- the service access token and URL;
- the LAN gateway hostname/IP address;
- the serial port path — a `/dev/serial/by-id/...` path contains the USB
  adapter's serial number. Direct USB mode reports only a category (`by-id`,
  `tty`, `other`); direct LAN reports its protocol. Service mode reports that
  it uses the service, without inventing adapter settings.
- the wallbox serial number — only a stable FNV-1a 64 hash of it. The raw serial-number
  register block is omitted too. This hash is a pseudonymous support
  identifier, not a cryptographic anonymization guarantee.

It includes the bus parameters, the device capabilities, the write counters
and a raw register image excluding identifying fields. Known identifiers are
also redacted when they appear inside transport error messages.

A bounded in-memory history retains at most 20 failed write attempts and
subsequent confirmations, with register addresses, software timings, packet
callback counts and predefined outcomes. It stores no packet contents or write
values and creates no Recorder rows or files. The service exposes this history
only through its authenticated diagnostics endpoint, requested on demand.

## Charging data

Charging sessions say when somebody was at home and when a vehicle was
charged. That data stays in your Home Assistant instance; if you export or
share it, you are the one deciding to share it.
