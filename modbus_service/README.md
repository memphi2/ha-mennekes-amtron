# Mennekes Amtron Modbus Service

Keeps the wallbox's Modbus connection, heartbeat and charging controls running
independently of Home Assistant Core. An HA restart does not stop this service.

The last requested charging state is held for as long as the service and bus
remain available, even if Home Assistant stays offline. A deliberate pause is
retained. Home Assistant reconnects without replaying old commands.

This is a local development build. No service image has been published yet.
See [setup and migration](../docs/modbus-service.md) for the local app export,
standalone container and existing-device migration.
