# Independent Modbus service

The service owns the USB RS-485 adapter and keeps the heartbeat running while
Home Assistant Core restarts. Home Assistant connects to its authenticated API;
it no longer opens the serial port. This uses the same separation of hardware
service and HA integration as Z-Wave JS, with an HTTP API instead of WebSocket.

**A Home Assistant outage keeps the last charging state indefinitely.** A pause
stays paused, a disabled charging release stays disabled, and the last current
setting remains active. HA automations cannot calculate new values while HA is
offline. Reconnecting HA reads the service state and sends no charging commands.

A host restart, service restart, power loss or USB fault can still interrupt the
heartbeat. The wallbox's configured fallback applies in that case. See
[Safety](safety.md#the-heartbeat-and-what-happens-without-it).

## Development status

The service and integration changes are local development work, not part of the
published 0.4.1 release. There is no published app image yet. The instructions
below build the current checkout. Installation and wallbox activation are
separate steps; building and testing do not change your running HA instance.

## Local Home Assistant OS app

Export a self-contained app build directory from the repository:

```bash
python scripts/build_service_app.py --output /tmp/mennekes-amtron-app
```

Copy that directory into your HA local apps/add-ons directory, refresh the app
store, and install **Mennekes Amtron Modbus Service**. The export includes the
Dockerfile and shared driver; it does not download a separate unpublished image.
The app starts in the `services` boot stage, independently of Core. Enable
**Start on boot** and the watchdog. The watchdog checks process liveness; a
wallbox connection fault is retried without repeatedly restarting the app.

Configure:

- **serial_port:** the stable adapter path, preferably `/dev/serial/by-id/...`.
- **device_id**, **baudrate**, **frame:** the wallbox's existing settings.
- **token:** a randomly generated secret of at least 32 characters, for example
  generated with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.
- **read_only:** initially `true`; set to `false` when ready for the service to
  maintain the heartbeat and accept charging commands.
- **current_cap:** the installation's permitted EMS current cap. The default is
  6 A. As with direct mode, an explicitly selected 0 A disables the EMS limit.

The API uses port 8765 on the app's internal network. Host port publication is
disabled by default. Use the app hostname shown by Supervisor as the integration's
service URL, for example `http://APP_HOSTNAME:8765`. Keep the token private.

The integration's service options control only HA's access and refresh interval.
Selecting read-only there disables HA commands while the service keeps its
heartbeat and charging state. Set the actual Modbus polling/measurement cadence
and the service's write permission in the service configuration. HA's optional
current cap can further restrict commands; it cannot raise the service's cap.

## Standalone container or Python service

Build the image from the repository root:

```bash
docker build -t amtron-modbus-service:local .
```

Create a private data directory containing `options.json`. Its structure is:

```json
{
  "serial_port": "/dev/ttyUSB0",
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

Map that directory to `/data`, map only the required serial device, and attach
the service and HA containers to the same private network. Give the service a
restart policy independent of HA. No privileged container or host networking
is needed. The service itself uses plain HTTP: use a trusted private network,
or an HTTPS reverse proxy for connections outside that network. Redirects are
not followed and credentials must not be embedded in the URL.

Alternatively, with Python 3.14+, install this checkout into its own virtual
environment using `pip install .`, then run:

```bash
amtron-modbus-service --config /path/to/options.json --state /path/to/intent.json
```

The installed package contains only the driver/service and its dependencies;
Home Assistant is not a service dependency. Run it under an independent process
manager with access to the serial device and state directory.

## Move an existing wallbox

1. Note the current pause, current limit and charging-release settings. In
   particular, note the resume current if the wallbox is paused.
2. Disable the existing HA integration entry so it releases the serial port.
   Do not delete the entry: it retains the device and entity IDs.
3. Start the service with the same bus settings, initially read-only. Check its
   status, then configure its current cap and enable control as described above.
4. Reconfigure the existing integration and choose **Connect to the Modbus
   service**. Enter the URL and token. The serial number must match the existing
   entry. Re-enable the entry if it remains disabled.
5. Check the readings, pause and current slider. The service reads the current
   wallbox state. On its first setup, an already paused wallbox without a saved
   resume limit uses a conservative 6 A resume target. If needed, resume explicitly
   and set the previously noted current when you intend to charge again.
6. Restart **Home Assistant Core** and verify that the service remains running,
   its heartbeat stays healthy and the selected pause/current is unchanged.

Do not start another serial integration or energy manager on the same bus.
Removing or disabling the HA entry does **not** stop the independent service.
To relinquish control, stop the service or configure its read-only mode and
check the wallbox's fallback behavior.

## State, diagnostics and failures

Only pause intent, charging-release intent and the resume current are saved in
`intent.json`, bound to the wallbox identity. The file is atomically replaced
with owner-only permissions when those settings change. It contains no growing
history. Heartbeats and polls do not create state-file entries or Recorder rows.
Include the app's data directory in backups; protect its options file/token.

The service enforces the same current-write floor, phase-switch guards, device
limits and bounded error-200 recovery as direct mode. All clients share one
serialized command path. Failed or timed-out commands are never automatically
replayed by the HA client, because the write may already have reached the box.
A persistent recovery failure stays visible. Clearing error 200 while paused
still needs confirmation on the actual wallbox firmware.

At most eight API bus requests can be in flight; excess requests fail promptly
instead of building an unbounded command queue. Heartbeats retain priority over
bus reads and commands. Normal state requests read the cache without bus traffic.

The API exposes named actions and an allowlist of diagnostic reads, with bearer
authentication and version checking. It offers no arbitrary register-write or
remote-shutdown endpoint. `/health` reveals process liveness only. If the service
or wallbox becomes unreachable, HA marks its readings unavailable; reconnecting
does not replay HA restore values.
