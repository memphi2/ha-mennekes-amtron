# Mennekes Amtron Modbus Service

Keeps the wallbox connection, heartbeat and selected charging state running
independently of Home Assistant Core. A deliberate pause remains active during
HA restarts and longer outages.

This is the **0.5.0b2 test release**. It requires exclusive ownership of the
RS-485 adapter and the matching Mennekes Amtron RTU integration.

See [setup and migration](DOCS.md) before handing the adapter over to the app.
