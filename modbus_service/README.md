# Mennekes Amtron Modbus Service

**Keep the heartbeat running while Home Assistant Core restarts.** This optional
service is the preferred way to connect the integration: the wallbox keeps its
last charging state, including a deliberate pause, independently of HA.

**Version 0.9.0b3 · Home Assistant app or standalone service**

**Open Web UI** starts on an overview of the wallbox and the app's recent
commands. Further pages set up the USB or LAN connection, show every register,
change polling intervals without a restart and record the Modbus traffic for ten
minutes. They need a Home Assistant administrator and Supervisor **2026.10.4 or
newer**. See [Web pages](DOCS.md#web-pages).

**Charging jobs** run in the app itself, also while Home Assistant is offline:
one order for the plugged-in car with a time window, a current and a maximum
amount of energy, with an optional early start of up to two hours. **Guest
charging** gives a locally authorized visitor a limited order. Neither switches
the charging release on, unlocks the wallbox or overrides a battery target.

Optional **Virtual Plug & Charge** lets HA grant charging for a recognized
vehicle after fresh cable/home detection. The app keeps an accepted grant
through HA Core restarts for the same physical connection; new connections need
fresh recognition. Deliberate pauses, locks and reached charging targets remain
effective. This is a convenience feature, not ISO 15118 vehicle authentication.
See [charging permission](DOCS.md#virtual-plug--charge).

The app also provides optional Waveshare adapter setup and TCP login,
configurable charging waits, vehicle-aware phase switching and AppArmor
protection. Queued timed-plan commands expire in the app even while HA is
offline.

| Using | Start here |
|---|---|
| Home Assistant OS | [App setup and everyday use](DOCS.md): discovery supplies the address and token. |
| Home Assistant Container or another host | [Docker/Python setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/service-standalone.md) |
| An existing direct USB or LAN connection | [Handover guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/modbus-service.md#move-an-existing-wallbox): keep your device and entity IDs. |

Install the Mennekes Amtron RTU integration separately, in the same version as
this app; update the app first. Its controls follow the app's **Read-only**
switch; there is no second HA write switch. After changing this setting, save
and restart the app; HA needs no restart. Direct USB and LAN connections remain
available without this service.

The service needs exclusive access to the RS-485 adapter. App, host, power and
USB failures can still interrupt the heartbeat, so configure the wallbox's
[fallback behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/safety.md#the-heartbeat-and-what-happens-without-it).

[LAN-to-RS-485 adapters](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/network-gateway.md) are also supported,
with transparent RTU over TCP or translating Modbus TCP gateways. No local USB
device is needed. Network and gateway outages can still interrupt the heartbeat.

[Technical reference](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.9.0b3/docs/service-reference.md) · [Changes](CHANGELOG.md)
