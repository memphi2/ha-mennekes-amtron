# Changes

## Unreleased

- Tolerate malformed optional firmware, article and serial text so other valid
  readings remain usable; existing serial-number identity checks still apply.
- Keep the latest requested current after a lost write reply or cancellation;
  an older queued slider value must not return later or after a service restart.
- Remove duplicate connection choices: only **USB / RS-485**, **LAN: Modbus RTU
  (TCP)** and **LAN: Modbus TCP** are shown. If an old technical name is saved,
  select its matching readable choice once; 0.5.1 supports doing this before
  the update. Existing readable selections and other settings stay unchanged.
- Add a custom AppArmor profile that Supervisor loads with protection mode on.
  App options stay read-only; the service can maintain its own token and charging
  state and communicate over USB/LAN. Native AMD64/ARM64 tests exercise all three
  transports under enforcement, including restart and rejected unrelated access.
- Document permission-denial troubleshooting and why an installed app's rating
  can differ from the app-store rating before installation.

## 0.5.1

- Always show **Adapter host / IP** in the app configuration. Enter it for LAN;
  leave it empty for USB. Choosing a protocol does not hide or reveal fields.
- Offer readable connection choices: **USB / RS-485**, **LAN: Modbus RTU (TCP)**
  and **LAN: Modbus TCP**. Existing `serial`, `rtu_over_tcp` and `modbus_tcp`
  settings remain valid; updating does not change the selected connection.
- Version the app independently from the integration and report the service
  version in API snapshots. Future integration-only updates can reuse a
  compatible app without interrupting its heartbeat. The integration still
  accepts the published 0.5.0 API 2 service.
- Keep existing app data and the HA entry when updating. If both components need
  an update, update the app first; its restart briefly interrupts the heartbeat.

## 0.5.0

- **Update this app or standalone service first**, then update the integration to
  **0.5.0** and restart Home Assistant Core. Keep the existing app data and HA
  entry. The old beta integration may temporarily show unavailable controls;
  after the app update, the running service retains its heartbeat and charging
  intent with writes enabled. The app update itself briefly interrupts heartbeat.
- Use API 2 with `/v2/` endpoints; beta API 1 clients are no longer accepted.
  Charging commands must match the service's wallbox identity and are refused
  when its data is unavailable or stale, including after waiting in the command
  queue. The integration reports incompatible versions separately from data errors.
- Connect to a LAN-to-RS485 adapter using transparent RTU-over-TCP or a
  translating Modbus TCP gateway. Choose the connection type, adapter host,
  TCP port and wallbox address in the app settings; USB is still supported.
- Make the serial-device setting optional for LAN installations. The gateway's
  TCP port is separate from the app's HTTP API port; HA app discovery is unchanged.
- Keep heartbeat and charging control in the independent service for either
  connection type, including through Home Assistant Core restarts.
- Stable release with automatic HA OS discovery and five UI languages. Existing
  beta installations receive 0.5.0 through their usual app update.
- Improve connection recovery, configuration validation and pause-state handling.

## 0.5.0b5

- Add French, Dutch and Italian app settings alongside English and German.
- Simplify installation and everyday guidance, with the optional service as
  the preferred connection and separate technical reference pages.
- Pair with integration 0.5.0b5: the app's **Read-only** setting is now the sole
  write-permission switch for service connections. HA controls stay registered
  and become available after fresh writable status; changing permission sends
  no HA charging commands. Both current caps still constrain limited-current
  commands; deliberately selected 0 A remains unlimited.
- Update the integration first and restart Home Assistant Core. The running
  beta-4 app already provides the required status; keep the existing entry.
  The app update is offered separately and briefly interrupts the heartbeat.
- Minimum/latest HA validation passed with coverage above 99%, including permission
  changes through real HA and HTTP. Hardware verification of this fix is pending.

## 0.5.0b4

- Treat unusable optional maximum-EVSE-current readings as an unknown capability
  while retaining the raw measurement and enforcing configured command limits.
- Pair with integration 0.5.0b4 to fix setup rejected with
  `invalid service snapshot (identity.max_evse_current)`. The updated integration
  also accepts these readings from an already-running beta-3 app.

## 0.5.0b3

- Supervise the heartbeat worker; unexpected exits stop the service and release
  the adapter so the enabled Supervisor watchdog can restart it.
- Include bounded service events and failed-write timing in downloaded diagnostics,
  without packet contents, credentials or additional Recorder writes.
- Release unloaded serial clients without waiting for cyclic garbage collection.
- Pair with integration 0.5.0b3 for automatic app lookup, current credentials on
  setup retry and clearer connection errors. Update both components.

## 0.5.0b2

- Fix startup with the baudrate strings saved by Supervisor selection fields.
- Discover the app in Home Assistant automatically, including URL and token.
- Remove the manual app-token option; the app exclusively manages its private token.
- Log service, API, serial and heartbeat lifecycle and name invalid options.
- Close serial ownership when a background worker exits unexpectedly.

## 0.5.0b1

- First beta of the independent Modbus service for amd64 and aarch64.
- Keeps the heartbeat and last charging state through Home Assistant Core restarts.
- Preserves intentional pause, charging release and remembered resume current.
- Authenticated API, bounded requests and change-only persistent control intent.
- Resume applies each HA client's current cap to the latest service setpoint
  under the current-write lock.
- Published container images are tested before the app catalog is updated.
- App installations use the verified image digest, so later tag changes cannot
  substitute a different container.

Physical wallbox and Supervisor acceptance testing is pending.
