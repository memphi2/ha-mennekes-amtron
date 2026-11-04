# Mennekes Amtron Modbus Service

This app keeps Modbus and the heartbeat running while Home Assistant Core
restarts. It holds the last charging state indefinitely while HA is offline,
including a deliberate pause. Host, app or USB outages still interrupt the
heartbeat and use the wallbox's configured fallback.

## First setup

Install the matching **0.5.0b2** integration first. Enable **Pre-release** for
its Marketplace repository on HA 2026.11+, or beta releases in HACS on older
supported versions. The integration and this app are installed separately.
Supervisor may show **Building** while it creates the local app image from the
verified download. No Python source is compiled or dependencies installed locally.

1. Note the pause, current limit and charging-release settings and disconnect
   the vehicle for the first handover. Disable the existing direct-serial
   integration entry to release the adapter. Keep the entry to retain its IDs.
2. Set the serial adapter path, address, baudrate and frame to match your wallbox.
3. Leave **Access token** empty to generate and store it automatically, or keep
   your existing token. No copying into the integration is needed.
4. Start with **Read-only** enabled and check the app log.
5. Enable **Start on boot** and **Watchdog**. Under **Settings → Devices & services**,
   configure the discovered **Mennekes Amtron** entry. Address and token are
   supplied automatically, and an existing entry for this wallbox keeps its IDs.
   Re-enable the existing entry if it was disabled for the handover.
6. Verify the readings while still read-only. Set the app's permitted current
   cap and disable **Read-only**. Set the integration's **Control mode** to
   **Modbus master** and its HA current cap; both permissions must allow writes.
7. Reapply and verify the noted pause, current and charging-release settings
   before reconnecting the vehicle. Test a Home Assistant Core restart.

Only this app may own the serial adapter. The default API port is available
inside the Supervisor network; there is no need to publish a host port.
Read-only in the HA integration blocks HA commands, but does not stop this app
or its heartbeat. To relinquish Modbus control, stop the app.

## Options and state

The log shows service/API start, the serial connection, heartbeat start/stop
and shutdown. Read-only mode reports that heartbeat delivery is disabled.
Connection and heartbeat health are logged only when they change. An immediate
configuration failure names the invalid option; check that message before
restarting again. A missing serial device is retried without exiting the app.

The default current cap is 6 A. Explicitly selecting 0 A in the integration
removes the EMS limit; use the pause control to pause. A first-time migration
of an already paused device uses 6 A as its resume target if no saved target
exists. Existing app state retains the previously accepted target.
On first installation the app reads the wallbox's current state. If fallback
changed it during the handover, it cannot infer the previous HA intention;
verify and reapply your settings before reconnecting the vehicle.
The integration may impose a lower current cap. The app applies it when
resuming, using its latest saved target; an explicitly unlimited target stays
unlimited.

The app saves only changed pause/release intent and the resume current in its
data directory. This is a small replaced file, not a history database. Include
app data in backups, which also contain the access token. The heartbeat sensor
records health transitions rather than every heartbeat.

## Beta channel

Version 0.5.0b2 is for hardware testing. Test installation, deliberate pause,
HA restart, service restart and reconnection before relying on it unattended.
Clearing error 200 while paused still needs firmware confirmation.

The beta repository URL ends in `#apps-beta`; stable releases use `#apps`.
Supervisor treats these as different apps. When moving between channels,
copy the options, stop the old app before starting the other one, and confirm the
newly discovered app. Keep the original app and its backup until migration is verified.

[Full setup and migration guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.0b2/docs/modbus-service.md)
