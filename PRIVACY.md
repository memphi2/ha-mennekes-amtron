# Privacy

## What this integration collects

Wallbox control has no cloud dependency or telemetry. The optional German
Module 3 tariff import makes the external requests described below.
Direct mode speaks Modbus through a local
USB/serial adapter or an outgoing TCP connection to a configured LAN-to-RS-485
gateway. Service mode exchanges wallbox readings and control commands
with the configured Modbus service URL. Keep that service on a trusted private
network, or protect its HTTP connection with HTTPS. The gateway Modbus connection
itself is unencrypted and has no authentication by default. The optional
Waveshare login has known protocol weaknesses and does not provide TLS
protection; restrict gateway access to a trusted private LAN. See
[Security](SECURITY.md#threat-model).

## What is stored in Home Assistant

- The serial port path, bus parameters and Modbus device address, in the
  config entry for direct USB mode. Direct LAN mode stores the gateway host,
  TCP port, connection protocol and device address. When optional TCP
  authentication is enabled, it also stores the adapter's existing password.
  That password is not retained when authentication is disabled. Service mode
  stores the service URL and access token in the config entry; protect HA
  backups accordingly.
- The wallbox serial number, if the firmware exposes it, as the config entry's
  unique id and in the device registry.
- Register values, as entity states and in Home Assistant's recorder database
  like any other entity.
- If configured, vehicle source entity IDs, optional vehicle-provider and device
  selections for departure and charging history,
  and electricity-price/tariff settings in the integration options. Optional
  charging estimates retain the latest completed cycle, latest cost result and
  bounded loss totals with one correctable match in HA's local storage, outside
  Recorder. They do not retain a growing vehicle-history or price-history list.
- Optional timed charging stores the selected source entities, calendar and
  exact event title in the integration options, plus one departure and its
  source/finished marker in local storage. The optional BavarianData adapter
  reads the last locally published timer sensors for the selected vehicle; no
  vehicle cloud refresh is requested. Timer weekdays and the vehicle time zone
  are optional local settings. The preconditioning blueprint's HA
  Text helper remembers the last attempted departure to avoid duplicate actions.
  A selected preconditioning action uses its vehicle integration, which may
  contact that vehicle's cloud service under its own privacy policy.

## What the independent service stores

Standalone service configuration contains its USB or LAN connection settings and
access token. LAN settings include the gateway host, TCP port and protocol,
plus the existing adapter password when optional TCP authentication is enabled.
The Supervisor app exclusively manages its own token in an owner-only
`service-token` file, reused across restarts. Supervisor stores the app's discovery
address and token and transfers them locally to Home Assistant; no network
broadcast or cloud discovery is involved. Protect Supervisor backups as well. Its
small `intent.json` file stores the wallbox identity, pause/release intent,
resume current, charging targets and the latest completed charging cycle. It is
replaced when that durable state changes, with no growing history. Heartbeats,
ordinary polls and progress within a running cycle are not persisted. Protect
service data and backups.

The app's **connection page**, available from 0.6.0b1, displays locally mapped adapter
names and paths to an authenticated administrator through Supervisor Ingress.
Those names can contain the adapter's serial number. Connection changes are
saved in the app's Supervisor options. The page does not send the service token
or saved adapter password to the browser. It reports only whether a password
is present; a blank password field reuses it only for the same gateway host
and TCP port. A newly entered password is sent locally to the app for the
explicitly requested configuration.

Optional adapter detection queries only the entered endpoint and shows its
model, firmware, native identifier and connection settings. A small temporary
in-memory cache binds detected devices to the administrator and current app
options; detection tokens expire after five minutes. The raw adapter settings
are not returned through the API or saved as a scan history. Explicit setup
may update the selected adapter's serial, TCP and authentication settings; it
does not change its existing password. This work makes no cloud requests and
does not run periodically or during normal reconnects. The HA user ID is used
transiently for access and request validation, without being saved or logged
by the page.

## What diagnostics contain

The diagnostics download is built from an allowlist. It deliberately omits:

- the service access token and URL;
- the optional adapter password and authentication challenge/response frames;
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

## Optional German tariff import

Selecting **Import for HA Home** in the Module 3 setup sends HA Home latitude and longitude to VNBdigital
to identify the local low-voltage network operator. Public tariff CSV data is
downloaded from Par14a-Database through GitHub. Both services see the requesting
IP address; GitHub receives no Home coordinates. No vehicle history, wallbox
identity or access token is sent.

Opening the settings or selecting manual entry does not make this request.
Imported values remain a draft until you save the section. A lookup already
performed cannot be undone by leaving that draft.

Optional yearly refresh repeats the lookup after the tariff year expires, with
one scheduled attempt per day, using the German tariff time zone, until a valid tariff is found, plus a retry after
an HA restart. Values are saved locally without a growing download history.
Disable yearly refresh to stop background imports.
