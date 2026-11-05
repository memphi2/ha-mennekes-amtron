# Run the Modbus service with Docker or Python

Use this guide for Home Assistant Container or a service on another host.
Home Assistant OS users can use the simpler [app installation](modbus-service.md).
Both options keep the heartbeat independent of Home Assistant Core.

**Current release: 0.5.0.** Install the integration first. The service and integration
are updated separately; use matching releases for a new installation.

## 1. Prepare the serial handover

The service must be the only Modbus master on the bus, including in read-only
mode. If HA currently owns the adapter, follow
[Move an existing wallbox](modbus-service.md#move-an-existing-wallbox) first:
note the controls, disconnect the vehicle and disable the existing direct entry.
Keep that entry to retain its device and entity IDs.

Configure the wallbox's [fallback behavior](safety.md#the-heartbeat-and-what-happens-without-it).
The independent service survives HA outages; service, host and USB failures still
interrupt the heartbeat.

## 2. Create the configuration

Create a private data directory containing `options.json`:

```json
{
  "serial_port": "/dev/ttyUSB0",
  "connection_type": "serial",
  "device_id": 50,
  "baudrate": 57600,
  "frame": "8n2",
  "token": "REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS",
  "read_only": true,
  "current_cap": 6,
  "listen": "0.0.0.0",
  "port": 8765
}
```

Replace the token placeholder with a random secret of at least 32 ASCII
characters without whitespace. Match the serial settings to the wallbox.
The default current cap is **6 A**; leave read-only enabled for the first check.
The service also accepts `scan_interval` and `measurement_interval`, which default
to 5 and 30 seconds. Protect this directory and its backups: they contain both
the credential and saved charging intent.

The API uses plain HTTP. Put it on a trusted private network or use an HTTPS
reverse proxy outside that network. Do not expose it to the Internet. The HA
client does not follow redirects, and credentials must not be embedded in its
URL. See [Security](../SECURITY.md#threat-model).

### LAN adapter configuration

For a LAN-to-RS-485 adapter, replace `serial_port`, `baudrate` and `frame` with:

```json
{
  "connection_type": "rtu_over_tcp",
  "host": "192.168.1.40",
  "tcp_port": 502
}
```

Keep the other settings, including `device_id`, token and read-only mode, from
the full example. Use `modbus_tcp` instead for a gateway that translates Modbus
TCP to RTU. Set the serial baud/frame on the adapter, and match its listening TCP
port. No local serial path or container device mapping is needed for LAN.
The service's HTTP `port` remains **8765**; it is independent of `tcp_port`.
See [LAN setup and network access](network-gateway.md).

## 3. Start the service

Choose Docker or Python. In either case, use a restart policy or process manager
that runs independently of Home Assistant.

### Docker

Copy the **standalone container reference** from the
[published app catalog](https://github.com/memphi2/ha-mennekes-amtron/tree/apps).
Use the complete `ghcr.io/memphi2/ha-mennekes-amtron-service@sha256:…` reference
as the image. The digest selects the verified release even if a registry tag
changes later. The same reference supports Linux amd64 and arm64.

Configure the container with:

| Setting | Value |
|---|---|
| Image | The full reference for the matching integration release |
| Data mount | Your private data directory mounted at `/data` |
| Device mapping | USB only: map the required serial adapter to the path in `options.json`. None for LAN. |
| Network | A private network reachable by HA |
| Restart policy | Independent of the HA container, for example `unless-stopped` |

No privileged container or host networking is needed. The published image starts
with `/data/options.json` and saves intent to `/data/intent.json`. Keep the data
mount when replacing the container. Save the image digest with your configuration
and deliberately choose a new release digest when updating.

### Python

With Python 3.14+, install the matching release checkout into its own virtual
environment using `pip install .`, then run:

```bash
amtron-modbus-service --config /path/to/options.json --state /path/to/intent.json
```

The package contains the driver/service and its dependencies; Home Assistant is
not a dependency. Give the process a writable state directory and either access
to the serial adapter or network access to the LAN gateway. Run it under an
independent process manager.

## 4. Connect Home Assistant

For a new wallbox, choose **Add integration → Mennekes Amtron RTU → Connect to the
Modbus service**. For an existing direct-serial entry, use **Reconfigure** and
choose the service connection. Enter the service URL, such as
`http://amtron-service:8765`, and the token from `options.json`. The hostname must
be reachable from HA; `localhost` refers to HA's own host or container.

This URL addresses our HTTP service, not the LAN gateway's Modbus port. The
service's `host` and `tcp_port` options separately select the gateway.

Standalone services use manual URL/token setup. Supervisor's automatic discovery
is available only for the HA OS app. During migration, the service's wallbox serial
number must match the existing entry. Re-enable that entry if it remains disabled.

Compare readings while read-only. Then set the permitted `current_cap`, change
`read_only` to `false`, save the file and restart the service. Configuration is
read at startup; this briefly interrupts the heartbeat. HA needs no restart. After
fresh writable status, HA enables the controls automatically; no additional HA
write switch is needed. The integration options can impose a lower current cap.
Both caps constrain limited-current commands; deliberately selecting **0 A**
removes the EMS limit while hardware limits remain in force.

HA does not replay old commands after permission changes or reconnection. The
service may still recover error 200 automatically by reasserting its saved
charging state, including a deliberate pause.

Reapply and verify the noted pause, current and charging-release settings before
reconnecting the vehicle. Complete the [handover checks](modbus-service.md#check-the-handover).

## Operation and updates

Use the HA controls for daily charging and the service configuration for write
permission and Modbus polling. Keep the state directory across restarts and
updates. A Core restart leaves the service running; a service update interrupts
its heartbeat briefly. Do not run the old and new service together on the bus.

For an update from a beta, install the 0.5.0 integration and restart Core first,
then replace the service with the matching stable image while retaining its data
directory. Unlike separate Supervisor app channels, replacing this container
with the same data mount preserves its saved service intent and credential.

[Everyday operation and troubleshooting](modbus-service.md#everyday-use) ·
[API, diagnostics and local development](service-reference.md)
