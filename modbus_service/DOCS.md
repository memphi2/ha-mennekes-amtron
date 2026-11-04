# Mennekes Amtron Modbus Service

This app keeps Modbus and the heartbeat running while Home Assistant Core
restarts. It holds the last charging state indefinitely while HA is offline,
including a deliberate pause. Host, app or USB outages still interrupt the
heartbeat and use the wallbox's configured fallback.

## First setup

Install the matching **0.5.0b1** integration first. Enable **Pre-release** for
its Marketplace repository on HA 2026.11+, or beta releases in HACS on older
supported versions. The integration and this app are installed separately.

1. Disable the existing direct-serial integration entry to release the adapter.
   Keep the entry so the wallbox and entity IDs can be retained.
2. Set the serial adapter path, address, baudrate and frame to match your wallbox.
3. Set a random access token of at least 32 characters. Keep it private and use
   the same token when connecting the integration.
4. Start with **Read-only** enabled and check the app log. Set the permitted
   current cap, then disable **Read-only** when the service should maintain
   the heartbeat and accept commands.
5. Enable **Start on boot** and **Watchdog**. In the existing integration,
   choose **Reconfigure → Connect to the Modbus service** and enter the app's
   internal hostname, port 8765 and token. Re-enable the entry if necessary.
6. Verify the readings and pause state, then test a Home Assistant Core restart.

Only this app may own the serial adapter. The default API port is available
inside the Supervisor network; there is no need to publish a host port.
Read-only in the HA integration blocks HA commands, but does not stop this app
or its heartbeat. To relinquish Modbus control, stop the app.

## Options and state

The default current cap is 6 A. Explicitly selecting 0 A in the integration
removes the EMS limit; use the pause control to pause. A first-time migration
of an already paused device uses 6 A as its resume target if no saved target
exists. Existing app state retains the previously accepted target.
The integration may impose a lower current cap. The app applies it when
resuming, using its latest saved target; an explicitly unlimited target stays
unlimited.

The app saves only changed pause/release intent and the resume current in its
data directory. This is a small replaced file, not a history database. Include
app data in backups, which also contain the access token. The heartbeat sensor
records health transitions rather than every heartbeat.

## Beta channel

Version 0.5.0b1 is for hardware testing. Test installation, deliberate pause,
HA restart, service restart and reconnection before relying on it unattended.
Clearing error 200 while paused still needs firmware confirmation.

The beta repository URL ends in `#apps-beta`; stable releases use `#apps`.
Supervisor treats these as different apps. When moving between channels,
copy the options, stop the old app before starting the other one, and reconfigure
HA's service URL. Keep the original app and its backup until migration is verified.

[Full setup and migration guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.0b1/docs/modbus-service.md)
