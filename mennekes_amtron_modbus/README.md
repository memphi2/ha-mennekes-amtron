# Mennekes Amtron Modbus Service

**Keep the heartbeat running while Home Assistant Core restarts.** This optional
service is the preferred way to connect the integration: the wallbox keeps its
last charging state, including a deliberate pause, independently of HA.

**Version 1.0.0b6 · Home Assistant app or standalone service**

**Open Web UI** starts on an overview of the wallbox and the app's recent
commands. Further pages set up the USB or LAN connection, show every register,
change polling intervals without a restart and record the Modbus traffic while
the log page is open, ending at most ten minutes after it is closed. They need a Home Assistant administrator and Supervisor **2026.10.4 or
newer**. See [Web pages](DOCS.md#daily-use).

**Charging** shows the energy target, cost target, target time and optional
vehicle charge target in one place. Only the departure plan of your own car can
start a charge; the other targets never do. The **Start guest charging** button
grants the current connection; the energy target, cost target and target time
end it, whichever comes first.
Intelligent vehicle planning is configured in Home Assistant and continues in
the app while HA is offline. The wallbox lock and electrical protections still
apply.

Optional **Virtual Plug & Charge** lets HA grant charging for a recognized
vehicle after fresh cable/home detection. The app keeps an accepted grant
through HA Core restarts for the same physical connection; new connections need
fresh recognition. A fresh recognized plug-in may lift a previous manual
pause; a restored grant after restart preserves it. Locks and reached targets
remain effective. This is a convenience feature, not ISO 15118 vehicle authentication.
See [charging permission](https://github.com/memphi2/ha-mennekes-amtron/blob/v1.0.0b7/docs/vehicle-setup.md#optional-virtual-plug--charge).

The app also provides optional Waveshare adapter setup and TCP login,
configurable charging waits, vehicle-aware phase switching and AppArmor
protection. Queued timed-plan commands expire in the app even while HA is
offline.

| Using | Start here |
|---|---|
| Home Assistant OS | [App setup and everyday use](DOCS.md): discovery supplies the address and token. |
| Home Assistant Container or another host | [Docker/Python setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v1.0.0b7/docs/service-standalone.md) |
| An existing direct USB or LAN connection | [Handover guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v1.0.0b7/docs/modbus-service.md#move-an-existing-wallbox): keep your device and entity IDs. |

Install the Mennekes Amtron RTU integration separately; update the app first.
The versions need not match: the integration checks the minimum app version
and API it supports. Its controls follow the app's **Read-only**
switch; there is no second HA write switch. After changing this setting, save
and restart the app; HA needs no restart. Direct USB and LAN connections remain
available without this service.

The service needs exclusive access to the RS-485 adapter. App, host, power and
USB failures can still interrupt the heartbeat, so configure the wallbox's
[fallback behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v1.0.0b7/docs/safety.md#the-heartbeat-and-what-happens-without-it).

[LAN-to-RS-485 adapters](https://github.com/memphi2/ha-mennekes-amtron/blob/v1.0.0b7/docs/network-gateway.md) are also supported,
with transparent RTU over TCP or translating Modbus TCP gateways. No local USB
device is needed. Network and gateway outages can still interrupt the heartbeat.

[Technical reference](https://github.com/memphi2/ha-mennekes-amtron/blob/v1.0.0b7/docs/service-reference.md) · [Changes](CHANGELOG.md)

Unofficial community project, not affiliated with MENNEKES. Provided as is,
without warranty; use at your own risk.
