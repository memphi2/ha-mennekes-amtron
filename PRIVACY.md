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
- If configured, vehicle source entity IDs, the selected BavarianData device ID,
  and electricity-price/tariff settings in the integration options. Optional
  charging estimates retain the latest completed cycle, latest cost result and
  bounded loss totals with one correctable match in HA's local storage, outside
  Recorder. They do not retain a growing vehicle-history or price-history list.

## What the independent service stores

Standalone service configuration contains its USB or LAN connection settings and
access token. LAN settings include the gateway host, TCP port and protocol.
The Supervisor app exclusively manages its own token in an owner-only
`service-token` file, reused across restarts. Supervisor stores the app's discovery
address and token and transfers them locally to Home Assistant; no network
broadcast or cloud discovery is involved. Protect Supervisor backups as well. Its
small `intent.json` file stores the wallbox identity, pause/release intent,
resume current, charging targets and the latest completed charging cycle. It is
replaced when that durable state changes, with no growing history. Heartbeats,
ordinary polls and progress within a running cycle are not persisted. Protect
service data and backups.

The next beta's **unreleased connection page** displays locally mapped adapter
names and paths to an authenticated administrator through Supervisor Ingress.
Those names can contain the adapter's serial number. Connection changes are
saved in the app's Supervisor options. The page does not send the service token
to the browser, request cloud data or keep a device-scan history. The HA user ID
is used transiently for access and request validation, without being saved or
logged by the page.

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
- optional vehicle/price source identifiers, vehicle charging history and the
  stored loss or cost estimates.

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
charged. This integration keeps that data in Home Assistant and, when used,
the local Modbus service. Optional vehicle-energy and price sources are read
from existing HA entities or BavarianData's local history action; this
integration does not request a cloud refresh. Other integrations have their
own privacy policies. If you export data, share diagnostics or share backups,
you decide to share their contents.
