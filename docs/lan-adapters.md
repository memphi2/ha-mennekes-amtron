# Set up a compatible LAN adapter

**Available in beta 0.7.0b3.** Direct connections need integration 0.7.0b3;
service connections need app/service 0.7.0b3 for these adapter features.
Stable 0.6.1 and beta 0.7.0b1 do not include them.

You can optionally configure a supported Waveshare adapter from the integration
or the service app. The setup reads the adapter you specify, shows its identity
and settings, and applies changes only when you confirm them.

For an adapter you have already configured, use the ordinary
[LAN connection](network-gateway.md). Automatic setup is optional.

## Supported adapters

| Single-channel model | Automatic setup |
|---|---|
| Waveshare RS485 TO ETH (B) | Firmware **1.523**, with the recognized parameter layout |
| Waveshare RS485 TO ETH (C) | Firmware **1.523**, with the recognized parameter layout |
| Waveshare RS485 TO POE ETH (B) | Firmware **1.523**, with the recognized parameter layout; tested on a physical adapter |

Waveshare describes these three variants as having the same software functions.
The B and C entries use that documented compatibility and synthetic tests;
physical verification currently covers only **RS485 TO POE ETH (B), 1.523**.
[Waveshare's product comparison](https://www.waveshare.com/rs485-to-eth-b.htm?sku=30303)
identifies the three variants.

The adapter must already have a reachable IPv4 address. Setup selects TCP-server
mode. If TCP login is already enabled, the adapter must already be in TCP-server
mode so its existing password can be verified before any change.
Detection checks its model, firmware, capabilities and parameter layout. A
different firmware, unknown layout, protected management configuration or a
different product is refused for automatic changes. Unknown extension data,
custom registration/heartbeat payloads and unrecognized framing flags must be
removed through the manufacturer's tools before this setup can be used. Ordinary manually
configured LAN connections remain available. RS232-only, Wi-Fi and multi-channel
models are outside this automatic setup profile; two-channel models need their
own implementation and verification.

No firmware update is performed. Use only an image explicitly intended for your
exact model; the shared software feature list is not permission to cross-flash.
Waveshare provides a [B-family firmware page](https://www.waveshare.com/wiki/RS485_TO_ETH_B_burns_firmware).
A matching C-model firmware download has not been independently confirmed here.

## Choose the connection settings

Match the wallbox's existing RS-485 settings. Factory defaults are **57600 baud,
8n2, Modbus address 50**. Changing the adapter does not change the wallbox's own
baud rate, frame or address.

| Setting | Choices |
|---|---|
| Baud rate | 9600, 14400, 19200, 28800, 38400 or 57600 |
| Frame | **8n2**, **8e1** or **8o1** |
| Connection protocol | Transparent **RTU over TCP**, or translating **Modbus TCP** |
| TCP port | The adapter's listening port, commonly **502**; separate from its web port |
| TCP login | Off, or the supported password handshake using the existing adapter web password |

Every confirmed adapter setup also applies a passive communication profile:
**TCP-server mode**, no storage or automatic query engine, no serial flow
control, no MAC preamble, no replay of buffered serial data, no automatic
parameter upload, and no restart triggered by missing application data. This
disables the adapter's known autonomous request and replay functions; other
Modbus masters must still be stopped. The profile is read back along with your
chosen baud rate, frame, protocol and port. Physical RS-485 traffic under this
complete profile has not yet been verified.
Unknown settings are refused rather than erased.

Transparent RTU over TCP is a straightforward choice for one wallbox and one
controller. Modbus TCP also works when both sides use the translating mode.
The adapter's heartbeat-packet feature does not replace the wallbox heartbeat;
the integration or independent service still sends it.

## Direct Home Assistant connection

1. Choose **Add integration → Mennekes Amtron RTU → Direct LAN adapter
   connection**, or use **Reconfigure** on the existing entry.
2. Enter the adapter host, desired TCP port and protocol, and the wallbox address.
   Enable **Set up LAN adapter**.
3. Check the recognized model and firmware. Select the baud rate and frame that
   match the wallbox. Choose whether TCP login should be enabled.
4. Submit the settings. The adapter briefly restarts, its settings are read back,
   and the integration checks the wallbox identity before completing setup.

Detection only contacts the entered host; it does not scan your network. When
reconfiguring, the existing direct connection is stopped before applying adapter
changes. A different wallbox cannot silently replace the existing device.

If TCP login is already configured on the adapter, leave **Set up LAN adapter**
off and select **TCP login** instead. This authenticates the connection without
changing the adapter.

## Service app

1. Open the app's **Web UI** as a Home Assistant administrator. Select a LAN
   connection and enter the adapter host.
2. Use adapter detection and review its model, firmware and identifier. Only
   this host is queried; detection itself changes nothing.
3. Select the protocol, port, baud rate, frame and TCP login settings you want
   to apply. Save the connection with adapter setup enabled.
4. After successful saving, **restart the app from its Info tab**. Check the
   connection log and Home Assistant readings.

Applying adapter settings stops the app's old Modbus connection before the
adapter restarts. It remains stopped until you restart the app with the saved
configuration. The heartbeat is interrupted during this change, so the wallbox's
configured fallback applies. Stop other bus owners before starting setup.
Saving ordinary connection settings without adapter setup keeps the existing
behavior: they take effect when the app next restarts.

## TCP login and its limits

TCP login uses the adapter's **existing web password**, not the app's service
token. The supported field is **1–9 printable ASCII characters without spaces**.
Set a nonempty password in the adapter's own web interface first if needed.
This integration never changes that password. When login is currently enabled,
the correct existing password is also required before disabling it.

The HA and app connection pages retain the adapter password only when TCP
login is used. They clear it when login is disabled or you switch to USB. Treat Home Assistant configuration, app options and backups as private.
The separate service API token remains automatic for the HA OS app.

A confirmed password rejection stops automatic login attempts. Correct the
password through Home Assistant's access-renewal prompt for a direct connection,
or correct it in the app and restart the app. Temporary timeouts and malformed
responses still retry automatically.

**TCP login does not encrypt Modbus traffic and does not provide TLS.** Firmware
1.523 has a confirmed handshake weakness that can accept a login without the
password. Acceptance was reproduced on the detached PoE B adapter; serial
command access through that bypass was not tested. Keep the adapter on a
trusted, isolated network and restrict access to the chosen HA/service host.
Do not rely on this login as a security boundary or expose the adapter to the
Internet.

## If applying settings fails

The setup verifies the complete passive profile and your selected settings. It
attempts to restore settings changed by this operation if validation or saving
fails. It preserves unrelated adapter settings, including its IP
configuration, web password and P2P options. Normal startup and reconnect never
rewrite adapter settings.

Restoration needs the same adapter to remain reachable. If restoration cannot
be confirmed, the old connection stays stopped: check the adapter locally
before trying again. This also applies when a sent change was never confirmed:
a delayed adapter update cannot safely be ruled out by one old readback.
An adapter changed by another administrator must be
detected again; do not reuse the old preview.

If a Supervisor save cannot be confirmed, the running app blocks further
connection saves while still allowing you to read its settings and detect the
adapter. A previous save may still complete later; a reload does not cancel it.
Check both the saved connection and the adapter before deciding how to recover.
The block applies only to this app process and does not prevent changes through
Supervisor or guarantee that an earlier request has finished after a restart.

For detection, allow the HA/service host to reach the adapter's **UDP port 1092**
and configured HTTP web port, as well as the selected TCP data port. There is no
broadcast discovery. Check [LAN troubleshooting](network-gateway.md#network-access-and-troubleshooting)
if the adapter connects but the wallbox does not answer.
