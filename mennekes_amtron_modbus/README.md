# Mennekes Amtron Modbus Service

**Keep the heartbeat running while Home Assistant Core restarts.** This optional
service is the preferred way to connect the integration: the wallbox keeps its
last charging state, including a deliberate pause, independently of HA.

**Beta 0.7.0b4 · Home Assistant app or standalone service**

This beta is available through **#apps-beta**; **#apps** keeps stable **0.6.1**.
Existing beta-catalog installations update in place. Keep their data and the
existing HA entry. For all beta fixes, update this app before integration
**0.7.0b4**. Switching app catalogs requires a separate handover.
See [Updates](DOCS.md#updates).

App settings are available in English, German, French, Dutch and Italian.
Use **Open Web UI** for the connection: choose a USB adapter from a dropdown,
or enter the LAN host/IP and port. Save, then restart the app from its Info tab.
This page needs an administrator and stable Supervisor **2026.11.0 or newer**;
read-only, limits and polling settings remain in the Configuration tab.

Beta 0.7.0b4 includes optional Waveshare adapter setup and TCP login in the connection
page, with clearer guidance and preserved input during adapter detection.
It also rechecks reached targets before delayed start commands are sent
and preserves previously saved tariff schedules. It retains the
charging targets, queued-command cancellation and the
data needed for optional HA charging-loss and cost estimates. Ordinary targets
only limit charging; an explicitly selected departure plan may also resume a
pause. Queued timed-plan commands expire in the service even while HA is offline.
The app also includes configurable charging waits, vehicle-aware phase switching
and AppArmor protection.

| Using | Start here |
|---|---|
| Home Assistant OS | [App setup and everyday use](DOCS.md) — discovery supplies the address and token. |
| Home Assistant Container or another host | [Docker/Python setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.7.0b4/docs/service-standalone.md) |
| An existing direct USB or LAN connection | [Handover guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.7.0b4/docs/modbus-service.md#move-an-existing-wallbox) — keep your device and entity IDs. |

Install the Mennekes Amtron RTU integration separately. App and integration
versions may differ: a compatible app keeps running during integration updates.
Its controls follow the app's **Read-only** switch; there is no second HA write
switch. After changing this setting, save and restart the app; HA needs no restart.
Direct USB and LAN connections remain available without this service.

The service needs exclusive access to the RS-485 adapter. App, host, power and
USB failures can still interrupt the heartbeat, so configure the wallbox's
[fallback behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.7.0b4/docs/safety.md#the-heartbeat-and-what-happens-without-it).

[LAN-to-RS-485 adapters](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.7.0b4/docs/network-gateway.md) are also supported,
with transparent RTU over TCP or translating Modbus TCP gateways. No local USB
device is needed. Network and gateway outages can still interrupt the heartbeat.

[Technical reference](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.7.0b4/docs/service-reference.md) · [Changes](CHANGELOG.md)
