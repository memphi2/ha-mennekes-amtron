# Mennekes Amtron Modbus Service

**Keep the heartbeat running while Home Assistant Core restarts.** This optional
service is the preferred way to connect the integration: the wallbox keeps its
last charging state, including a deliberate pause, independently of HA.

**Beta 0.8.0b1 · Home Assistant app or standalone service**

This beta is available through **#apps-beta**; **#apps** keeps stable **0.6.1**.
Existing beta-catalog installations update in place. Keep their data and the
existing HA entry. For all beta fixes, update this app before integration
**0.8.0b1**. Switching app catalogs requires a separate handover.
See [Updates](DOCS.md#updates).

App settings are available in English, German, French, Dutch and Italian.
Use **Open Web UI** for the connection: choose a USB adapter from a dropdown,
or enter the LAN host/IP and port. Save, then restart the app from its Info tab.
This page needs an administrator and Supervisor **2026.10.4 or newer**.
Read-only, limits and polling settings remain in the Configuration tab.

New in beta **0.8.0b1**: optional **Virtual Plug & Charge** lets HA grant charging
for an individually permitted vehicle after fresh cable/home detection. The app
keeps an accepted grant through HA Core restarts for the same physical connection;
new connections need fresh recognition. Deliberate pauses, locks and reached
charging targets remain effective. This is a convenience feature, not ISO 15118
vehicle authentication. See [charging permission](DOCS.md#virtual-plug--charge).

The new **Live Modbus communication** panel shows decoded existing reads, writes
and heartbeats when you enable it. Capture is off by default, limited to ten
minutes and 200 entries in RAM, and creates no extra Modbus requests or history.

The app also provides optional Waveshare adapter setup and TCP login, configurable
charging waits, vehicle-aware phase switching and AppArmor protection. Ordinary
charging targets only limit charging; an explicitly selected departure plan may
also resume a pause. Queued timed-plan commands expire in the service even while
HA is offline. This beta improves target saving and keeps TCP-login fields visible
in native Configuration.

| Using | Start here |
|---|---|
| Home Assistant OS | [App setup and everyday use](DOCS.md) — discovery supplies the address and token. |
| Home Assistant Container or another host | [Docker/Python setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b1/docs/service-standalone.md) |
| An existing direct USB or LAN connection | [Handover guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b1/docs/modbus-service.md#move-an-existing-wallbox) — keep your device and entity IDs. |

Install the Mennekes Amtron RTU integration separately. App and integration
versions may differ: a compatible app keeps running during integration updates.
Its controls follow the app's **Read-only** switch; there is no second HA write
switch. After changing this setting, save and restart the app; HA needs no restart.
Direct USB and LAN connections remain available without this service.

The service needs exclusive access to the RS-485 adapter. App, host, power and
USB failures can still interrupt the heartbeat, so configure the wallbox's
[fallback behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b1/docs/safety.md#the-heartbeat-and-what-happens-without-it).

[LAN-to-RS-485 adapters](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b1/docs/network-gateway.md) are also supported,
with transparent RTU over TCP or translating Modbus TCP gateways. No local USB
device is needed. Network and gateway outages can still interrupt the heartbeat.

[Technical reference](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.8.0b1/docs/service-reference.md) · [Changes](CHANGELOG.md)
