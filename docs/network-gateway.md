# Connect through a LAN adapter

A LAN-to-RS-485 adapter lets the supported RTU wallbox connect without a USB
adapter on your HA or service host. The wallbox still uses the same RS-485
register interface; only the connection to its bus changes.

**Available from 0.5.0.** Physical testing of particular gateways remains separate
from automated protocol tests.

## Choose the adapter protocol

Check the adapter's configured operating mode, not just its product name.
These two modes are different and must match the integration/service setting:

| Adapter mode | App choice | Standalone setting | What travels over the network |
|---|---|---|---|
| Transparent serial server | **LAN: Modbus RTU (TCP)** | `rtu_over_tcp` | Modbus RTU frames, including their CRC, passed through to RS-485. |
| Modbus TCP-to-RTU gateway | **LAN: Modbus TCP** | `modbus_tcp` | Modbus TCP requests that the gateway translates to RTU on the bus. |

For direct HA connections, select **RTU over TCP** or **Modbus TCP** in the
integration's LAN form. The app's default **USB / RS-485** is for a local adapter.

Set the adapter to accept a TCP client connection. Configure its RS-485 baud
rate and frame to match the wallbox: factory defaults are **57600 baud, 8 data
bits, no parity, 2 stop bits**. These settings belong on the adapter for LAN
connections. For recognized Waveshare models, optional [LAN adapter setup](lan-adapters.md)
can apply those settings from HA or the app after confirmation.
Manual setup must also disable storage/cache polling, automatic serial queries,
registration/heartbeat packets and serial-buffer replay. A converting adapter
must use request/reply mode; the manufacturer's default storage mode can issue
its own requests and is unsuitable for exclusive bus ownership.

Use the wallbox's Modbus address behind the gateway: factory default **50**,
supported range **10–50**. Check that the gateway forwards that address unchanged.
The TCP port defaults to **502** but must match the adapter's actual listening
port. A transparent adapter may use a different port.

This does **not** add support for Mennekes wallboxes with a different native
Modbus TCP register interface. Supported models remain those in the
[RTU compatibility matrix](compatibility.md).

## Choose who runs Modbus

| Connection owner | What to configure |
|---|---|
| **Modbus service — preferred, optional** | Select the matching LAN protocol above, then enter the adapter host and TCP port. HA continues to connect to the service. |
| **Home Assistant directly** | Choose **Add integration → Mennekes Amtron RTU → Direct LAN adapter connection** and enter the same protocol, host, TCP port and wallbox address. |

The independent service keeps the heartbeat running through HA Core restarts
with either USB or LAN. A direct HA LAN connection stops its heartbeat during a
Core restart, just like direct USB. A gateway, network, service or host outage
can still interrupt delivery; configure the wallbox's
[fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).

### Service app

App **0.6.1** includes **Open Web UI**. Older app 0.5.2b2 uses the native
Configuration tab described below.

1. Follow the [service installation](modbus-service.md) and prepare any existing
   connection for handover before starting a second bus owner.
2. If the app offers **Open Web UI**, start it and open that page. It requires an
   administrator and stable Supervisor **2026.10.4+**. Select **LAN: Modbus RTU
   (TCP)** or **LAN: Modbus TCP**, matching the gateway. Enter the adapter's host/IP
   without `http://` or a URL path, and its TCP port. The page shows the LAN fields
   and removes any saved USB path when you save.
3. On older apps, use the native **Configuration** tab instead. The host field is
   always visible from app 0.5.1; those older apps may require clearing an obsolete
   USB selection before saving. USB baud/frame fields are ignored for LAN;
   configure those on the gateway instead. Keep **Read-only** on initially.
4. Save and restart the app. Confirm its HA discovery and compare readings.
   When ready, disable **Read-only**, save and restart the app again.

No local USB device is required. The app's read-only switch still controls HA
write permission. No additional write switch is needed in HA.

For a standalone service, use the [LAN configuration example](service-standalone.md#lan-adapter-configuration).

### App 0.5.0 configuration

If you are still on app 0.5.0, update it to 0.6.1 before updating the
integration. That older app lists the values
`serial`, `rtu_over_tcp` and `modbus_tcp`. App 0.5.1 shows those values alongside
the readable choices. App 0.5.2b1 and newer keep only the readable choices; before updating from
0.5.1, select the matching readable choice once. See
[Updating the connection choice](upgrading.md#app-connection-choice).

If 0.5.0 shows the TCP port without a host field, enable **Show unused optional
configuration options** on the app's Configuration tab. Alternatively, open its
YAML editor and add `host: "192.168.1.123"`, replacing that example with your
adapter's address. Save and restart the app to apply it. Changing the connection
choice alone does not reveal the field. An app restart briefly interrupts the
heartbeat; HA does not need a restart for this setting.

### Direct Home Assistant connection

Open the LAN form and enter the adapter protocol, hostname/IP, TCP port and
wallbox address. It checks the device before creating the entry. There is no
baud-rate or address scan over the network; use the adapter and wallbox settings.
New entries start read-only. Check readings, then select **Modbus master** in the
integration options to enable controls and the heartbeat.

For an existing entry, use **Reconfigure → Change LAN adapter connection**.
Keep its device and entity IDs; the serial number must match the existing
wallbox. Stop the previous bus owner before enabling the new connection.

## Gateway address versus service URL

| Field | Belongs to | Example |
|---|---|---|
| **Adapter host + TCP port** | The Ethernet-to-RS-485 adapter | `192.168.1.40` and `502` |
| **Service URL** | Our independent Modbus service's HTTP API | `http://amtron-service:8765` |

These endpoints are not interchangeable. A Modbus gateway is not the independent
service and does not accept its token or HTTP requests. On HA OS, the app's
service URL and token are discovered automatically even when the app reaches
the wallbox through LAN.

## Network access and troubleshooting

Ordinary adapter connections have no authentication or encryption. Supported
Waveshare adapters optionally offer [TCP login](lan-adapters.md#tcp-login-and-its-limits),
which does not encrypt traffic and has a known firmware weakness. Keep the
adapter on a trusted private network, restrict access to the selected HA/service
host, and do not expose it to the Internet. The service token protects the
HA-to-service API; it does not protect the gateway's Modbus port.

Only one controller may use the wallbox bus. Multiple TCP clients or a gateway
with other masters behind it do not make simultaneous control safe. Read-only
also sends requests. See [Safety](safety.md#modbus-rtu-uses-a-single-master).

### Troubleshooting

| Symptom | Check |
|---|---|
| Connection refused or timeout | Host, TCP port, firewall, adapter power and TCP-server mode. |
| TCP connects but no valid readings | Transparent versus translating protocol, wallbox address, adapter baud/frame and RS-485 wiring. |
| Wrong wallbox during reconfigure | The gateway route and device address; identity checks prevent replacing the existing device silently. |
| Intermittent heartbeat | Network/gateway interruptions, competing clients and the serial bus; download diagnostics before restarting. |
| HA asks for a service token | Use the **Direct LAN adapter** form for the gateway, or connect through our installed Modbus service. |
