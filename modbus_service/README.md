# Mennekes Amtron Modbus Service

**Keep the heartbeat running while Home Assistant Core restarts.** This optional
service is the preferred way to connect the integration: the wallbox keeps its
last charging state, including a deliberate pause, independently of HA.

**Beta 0.5.2b2 · Home Assistant app or standalone service**

This prerelease is offered through `#apps-beta`; stable remains **0.5.1** in
`#apps`. See [Beta testing](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b2/docs/installation.md#beta-testing-052b2)
for integration installation and app updates.

App settings are available in English, German, French, Dutch and Italian.
**Next beta, unreleased:** use **Open Web UI** for the connection. USB adapters appear in a dropdown, while
LAN shows host/IP and port. Save, then restart the app from its Info tab.
This page needs an administrator and stable Supervisor **2026.11.0 or newer**;
read-only, limits and polling settings remain in the Configuration tab.

**New in 0.5.2b2:** a configurable state-change interval (default
**300 seconds**) queues early pause/resume, phase, release and lock changes.
HA shows the pending action and due time; stop requests also wait and replace
older pending starts.

| Using | Start here |
|---|---|
| Home Assistant OS | [App setup and everyday use](DOCS.md) — discovery supplies the address and token. |
| Home Assistant Container or another host | [Docker/Python setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b2/docs/service-standalone.md) |
| An existing direct USB or LAN connection | [Handover guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b2/docs/modbus-service.md#move-an-existing-wallbox) — keep your device and entity IDs. |

Install the Mennekes Amtron RTU integration separately. App and integration
versions may differ: a compatible app keeps running during integration updates.
See [Updates](DOCS.md#updates) for minimum-version requirements. Its service controls
follow the app's **Read-only** switch; there is no second HA write switch.
After changing this setting, save and restart the app; HA needs no restart.
Direct USB and LAN connections remain available without this service.

The service needs exclusive access to the RS-485 adapter. App, host, power and
USB failures can still interrupt the heartbeat, so configure the wallbox's
[fallback behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b2/docs/safety.md#the-heartbeat-and-what-happens-without-it).

[Technical reference](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b2/docs/service-reference.md) · [Changes](CHANGELOG.md)

[LAN-to-RS-485 adapters](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.2b2/docs/network-gateway.md) are also supported,
with transparent RTU over TCP or translating Modbus TCP gateways. No local USB
device is needed. Network and gateway outages can still interrupt the heartbeat.
