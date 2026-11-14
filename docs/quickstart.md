# First connection and first charge

[User guide](README.md) · [Dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Use this checklist after [Installation](installation.md). For HA OS with the
recommended app, follow [App setup](modbus-service.md) instead; return here for
[the first-charge check](#7-first-charge).

## 1. Prepare the hardware

Have an electrician prepare the [wallbox and RS-485 wiring](hardware.md).
Only one Modbus master may use the bus, including in read-only mode.

## 2. Install the integration

Follow [Installation](installation.md#2-install-the-integration), then return
here. Do not delete an existing wallbox entry to change connection methods.

## 3. Make the adapter available

USB: give the adapter to the chosen owner and use its stable
`/dev/serial/by-id/...` path. [USB help](hardware.md#making-the-adapter-visible-to-home-assistant).
LAN: configure the adapter's protocol, host, TCP port and serial settings.
[LAN setup](network-gateway.md) explains the fields; no local USB is needed.

## 4. Check fallback behavior

Prepare the wallbox's [fallback](safety.md#the-heartbeat-and-what-happens-without-it)
and keep the vehicle disconnected during the first control setup.

## 5. Add the wallbox

### Through the service

Follow [Service setup](modbus-service.md) to install and start it in **Read-only**
mode. On Home Assistant OS, confirm the discovered wallbox under
**Settings → Devices & services**; its URL and token are supplied automatically.

Start the app, choose USB or LAN under **Open Web UI → Connection**, save and
restart it. The page needs **Supervisor 2026.10.4+**; native **Configuration**
also remains available. Until a connection is configured, the app stays in setup
mode without Modbus or discovery.

For a standalone service, use **Add integration → Mennekes Amtron RTU →
Connect to the Modbus service** and enter its URL and token as described in that
guide.

### Direct serial connection

Open **Settings → Devices & services → Add integration → Mennekes Amtron RTU**.
Choose **Direct serial connection**, select the adapter and enter the wallbox's
bus settings. Factory defaults are:

| Setting | Value |
|---|---|
| Device address | `50` |
| Baud rate | `57600` |
| Data bits / parity / stop bits | `8` / `N` / `2` |

The form verifies communication before creating an entry. If the settings are
unknown, use its search option. A search across all bus settings can take several
minutes. New direct entries start read-only, with controls and heartbeat disabled.

### Direct LAN adapter connection

Choose **Direct LAN adapter connection**. Enter the protocol matching your
gateway (**RTU over TCP** or translating **Modbus TCP**), its hostname/IP,
TCP port (default **502**) and the wallbox address. Baud rate and frame are
configured on the adapter. Use **Reconfigure** for an existing wallbox.
The [LAN guide](network-gateway.md) explains the protocol choice and trusted-network
requirements. Do not enter a gateway address in the **Service URL** field.

## 6. Check readings, then enable control

Compare the readings with the wallbox before allowing writes. Set an appropriate
current cap, then enable control in the place that owns the USB or LAN connection:

| Connection | Enable control |
|---|---|
| Service | Disable **Read-only** in the app or service configuration, save and restart the service. HA controls become available after the next status refresh. There is no second HA write switch. |
| Direct USB or LAN | Open **Configure → Wallbox → Current limit and polling** and change **Control mode** to **Modbus master**. The entry reloads, starts the heartbeat and adds controls. |

For the service, the app's current cap is the only one. Resolve
any repair messages and check the `heartbeat` attribute of **Connection
quality** before charging.

## 7. First charge

1. Set **Charging current limit** to 6 A, then connect the vehicle and complete
   any required authorization. The wallbox must be unlocked and charging released.
2. Check **Charging**, **Signaled current** and **Total power**, then raise the
   limit as appropriate. The vehicle also determines whether and how much it draws.
3. Test **Charging paused**: on requests pause; off requests resume.
   Early pause and resume requests are queued for the configured interval
   (default 300 seconds);
   check **Pending change** and its `due` attribute. **0 A removes the current limit; it does not
   stop charging.**

You should now see changing power/energy readings, **Connection quality** with
`heartbeat: ok` once writes are enabled, and the expected pause state. If a request is waiting, leave it
queued or cancel it; do not repeatedly restart the app to force it.

Continue with [everyday use](user-guide.md), [optional functions and examples](features.md)
or [Recorder settings](recorder.md). A car integration is optional, including
when several cars use the wallbox.

## Optional command-line bus check

Normal setup needs no shell. The [read-only diagnostic check](troubleshooting.md#optional-command-line-bus-check)
is available for connection troubleshooting after stopping the current bus owner.
