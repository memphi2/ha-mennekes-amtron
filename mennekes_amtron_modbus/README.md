# Mennekes Amtron Modbus Service

**Keep the heartbeat running while Home Assistant Core restarts.** This optional
service is the preferred way to connect the integration: the wallbox keeps its
last charging state, including a deliberate pause, independently of HA.

**Current beta: 0.5.0b5 · Home Assistant app or standalone service**

App settings are available in English, German, French, Dutch and Italian.

| Using | Start here |
|---|---|
| Home Assistant OS | [App setup and everyday use](DOCS.md) — discovery supplies the address and token. |
| Home Assistant Container or another host | [Docker/Python setup](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.0b5/docs/service-standalone.md) |
| An existing direct-serial entry | [Handover guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.0b5/docs/modbus-service.md#move-an-existing-wallbox) — keep your device and entity IDs. |

Install the Mennekes Amtron RTU integration separately. Its service controls
follow the app's **Read-only** switch; there is no second HA write switch.
After changing this setting, save and restart the app; HA needs no restart.
Direct serial operation remains available without this service.

The service needs exclusive access to the RS-485 adapter. App, host, power and
USB failures can still interrupt the heartbeat, so configure the wallbox's
[fallback behavior](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.0b5/docs/safety.md#the-heartbeat-and-what-happens-without-it).

[Technical reference](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.0b5/docs/service-reference.md) · [Changes](CHANGELOG.md)
