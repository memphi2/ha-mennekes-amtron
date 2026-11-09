# Quick start

Use this checklist after [installing the integration](installation.md). First
make ordinary charging work; add vehicle and scheduling features afterwards.

Choose how Home Assistant should reach the wallbox:

| Connection | When to use it |
|---|---|
| **Independent Modbus service** | Recommended on Home Assistant OS: its app keeps the heartbeat running through HA Core restarts. A standalone service is also available for other installations. |
| **Direct serial connection** | Supported without an additional service. Home Assistant owns the adapter; a Core restart interrupts the heartbeat. |

Either connection owner can also use a [LAN-to-RS-485 adapter](network-gateway.md)
instead of USB. No USB device is needed on the HA or service host.

The service is optional. If your wallbox is already configured, keep its entry.
For a change to the service, follow the [handover guide](modbus-service.md#move-an-existing-wallbox)
instead of adding it again.

## 1. Prepare the hardware

Have a qualified electrician prepare the wallbox and RS-485 connection using
its installation manual and the [hardware guide](hardware.md). Modbus satellite
mode is required. Check the [single-master rule](safety.md#modbus-rtu-uses-a-single-master)
before connecting software to the bus.

## 2. Install the integration

Follow [Installation](installation.md) for the Marketplace or HACS, including
the release channel and any required Home Assistant restart. The service app
is installed separately; its steps are in the [service guide](modbus-service.md).
For an existing installation, see [Updating](upgrading.md).

## 3. Make the adapter available

Give the USB RS-485 adapter to the selected connection owner: the service app
or standalone service, or Home Assistant for direct serial mode. Prefer a stable
`/dev/serial/by-id/...` path. If the adapter is missing, check
[USB passthrough and permissions](hardware.md#making-the-adapter-visible-to-home-assistant).

For LAN, make the gateway reachable from that host and match its operating mode,
TCP port and wallbox serial settings. Follow [LAN setup](network-gateway.md);
USB passthrough does not apply.

## 4. Check fallback behavior

Configure the wallbox's intended [energy-manager fallback](safety.md#the-heartbeat-and-what-happens-without-it)
before enabling writes. The service protects against HA Core restarts; stopping
the service, restarting its host or losing the USB/LAN connection can still
interrupt the heartbeat. Keep the vehicle disconnected during initial control setup.

## 5. Add the wallbox

### Through the service

Follow [Service setup](modbus-service.md) to install and start it in **Read-only**
mode. On Home Assistant OS, confirm the discovered wallbox under
**Settings → Devices & services**; its URL and token are supplied automatically.

Start the app, choose USB or LAN in **Open Web UI**, save and restart it. The
guided page needs **Supervisor 2026.10.4+**; native **Configuration** also
remains available. Until a connection is configured, the app stays in setup mode without
Modbus or discovery. [Connection-page details](upgrading.md#app-connection-page).

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

For the service, HA's entry options offer an additional current cap. Resolve
any repair messages and check the **Heartbeat** sensor before charging.

## 7. First charge

1. Set **Charging current limit** to 6 A, then connect the vehicle and complete
   any required authorization. The wallbox must be unlocked and charging released.
2. Check **Charging**, **Signaled current** and **Total power**, then raise the
   limit as appropriate. The vehicle also determines whether and how much it draws.
3. Test **Charging paused**: on requests pause; off requests resume.
   Early pause and resume requests are queued for the configured interval
   (default 300 seconds);
   check **Pending change** and **Pending change due**. **0 A removes the current limit; it does not
   stop charging.**

You should now see changing power/energy readings, a healthy heartbeat with
writes enabled, and the expected pause state. If a request is waiting, leave it
queued or cancel it; do not repeatedly restart the app to force it.

Continue with [everyday use](user-guide.md), [optional functions and examples](features.md)
or [Recorder settings](recorder.md). A car integration is optional, including
when several cars use the wallbox.

## Optional command-line bus check

Normal setup needs no shell. The [read-only diagnostic check](troubleshooting.md#optional-command-line-bus-check)
is available for connection troubleshooting after stopping the current bus owner.
