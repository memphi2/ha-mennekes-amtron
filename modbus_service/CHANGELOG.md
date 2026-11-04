# Changes

## 0.5.0b2

- Fix startup with the baudrate strings saved by Supervisor selection fields.
- Discover the app in Home Assistant automatically, including URL and token.
- Generate and persist a private token when the app token is empty.
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
