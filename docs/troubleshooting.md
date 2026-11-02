# Troubleshooting

The diagnostics download includes `runtime_events`, a passive history of the
last 40 connection transitions, heartbeat failures/recoveries, late heartbeats,
write rejections/cooldowns and error-recovery attempts. Times are seconds since
the entry was loaded. Events contain predefined names and optional register
addresses, never raw error text, serial paths, serial numbers or register values.
The history is kept only in memory and resets on reload/restart. It adds no
entities, Recorder rows, polling, file writes or background tasks.

For downloading, updating or removing files, start with
[Installation and updates](installation.md). Wallbox communication itself is
local over RS-485 and does not depend on Marketplace or GitHub availability.

## The config flow cannot connect

Start with the adapter and bus before trying a broad address search:

1. Check that Home Assistant can see and open the USB adapter. For a virtual
   machine or container, check USB/device passthrough and permissions. Select
   its stable `/dev/serial/by-id/...` path when available.
2. Ensure no other entry, serial tool, energy manager or control box uses the
   same port/bus. **Read-only and discovery still transmit requests.**
3. Confirm the device address and frame. Factory defaults are address **50**,
   **57600 baud, 8 data bits, no parity, 2 stop bits**.
4. Have the installer check satellite mode and wiring against the
   [hardware guide](hardware.md) and manufacturer instructions.

The form can search addresses at your chosen settings, or all documented baud
rates and frames. The wider search takes several minutes. It does not write
registers or send a heartbeat, but still needs exclusive bus access. A report
that the port could not be used means repeated attempts to open it failed;
check access and ownership before changing wiring.

No result does not prove a wiring defect: permissions, port ownership,
settings, device mode and unexpected responses can also be responsible. The
[optional bus check](quickstart.md#optional-command-line-bus-check) can help
separate serial/device problems from integration setup.

## The device answered but reported no layout version

A response was received, but the integration could not identify a supported
register layout. Check the model, satellite configuration and device address.
Another device may be answering at that address. Download diagnostics or save
the redacted bus-check output when reporting the issue; do not assume a model
is supported solely because it answers Modbus requests.

## Writes are refused

First check **Control mode** in the entry options. Read-only deliberately
refuses writes. In master mode, a device rejection can indicate satellite
configuration, an unsupported register, a device-state restriction or
conflicting bus access. A rate-limit error instead names the waiting interval;
wait before retrying it. Use the exact error and repair message to distinguish
these cases.

## Error 200, "Energy manager unavailable"

The wallbox did not receive a usable heartbeat in time. In master mode,
**Recover from error** or the repair issue's **Fix** runs the documented
heartbeat → charging release → current sequence. Recovery can resume charging;
check the intended current first. A recent current write or pause may delay
recovery: wait for the reported interval, then retry.

If the error returns, investigate the cause before enabling automatic recovery:

- Home Assistant restarts, entry reloads or switching to read-only stop the
  heartbeat temporarily or permanently.
- USB disconnects, bus conflicts, wiring/termination problems and long Modbus
  timeouts can interrupt communication.
- A delayed event loop or slow/unresponsive device can make heartbeat writes late.

Diagnostics distinguish **late** heartbeat attempts from **failed** writes.
A failure can also mean a lost response; it does not prove whether the wallbox
received the request. Logs and counters help narrow the cause but do not by
themselves identify a defective cable.

Configure an intentional [energy-manager fallback](safety.md#the-heartbeat-and-what-happens-without-it)
with the manufacturer's tool. The integration raises a repair issue when
fallback is disabled.

## Some sensors became unavailable

A failed register block makes its affected entities unavailable until a
successful retry. Other blocks can keep working. A persistent failure of one
range can indicate unsupported firmware behavior; transient failures can also
come from the bus. The diagnostics field `failed_blocks` identifies the range.

## Entities are missing

- Controls exist only in **Modbus master** mode.
- Older layouts expose fewer entities; phase selection also needs capable hardware.
- Some diagnostic entities are disabled by default. Enable them in their entity
  settings when needed.

The **Modbus layout version** diagnostic sensor is disabled by default. Enable
it and compare the result with the **Needs** column in the
[entity reference](entities.md). An older-layout repair message explains the
limited feature coverage.

## The charging current does not follow

| Observation | Check |
|---|---|
| A new setting is delayed | Current writes have a five-second floor. The latest accepted request is written after that interval. |
| The offered current is lower than requested | Wallbox/installation limits and an active downgrade can lower **Signaled current**. The vehicle can draw less again. |
| A current change is rejected while paused | A normal current command would resume charging and must respect the pause/resume interval. Switching the pause control off obeys the same interval; it is not a bypass. |
| The slider shows a value while charging is paused | It shows the remembered setpoint, not actual current. Check **Charging paused**, **Signaled current** and **Total power**. |
| A manual pause is undone later | Check automations writing the pause switch or current. The pause/resume PV blueprint is allowed to resume charging; disable it to keep a manual pause. |

## PV automation stopped after an update

Open the **Charge from PV surplus** automation and select its **Charging
paused** input. The updated blueprint sends no commands until that input is
configured. Check that all selected entities belong to the same wallbox and
that the power sensor has a finite numeric value in **W or kW**.

Read [Blueprint updates](blueprints.md#updating-existing-blueprint-automations)
for edited copies and old paths. Use the automation trace to inspect trigger
and condition failures. Threshold hold timers are reset by Home Assistant
restarts and automation reloads.

## Phase switching leaves charging paused

That is expected when switching during charging: the integration pauses first,
requests the phases and leaves resuming to you. Wait for the required interval,
then resume explicitly. Check your vehicle's phase-switching support; some
vehicles abort the session. The integration exposes this control only on
hardware that reports support.

## The database keeps growing

A slower measurement interval reduces state-history traffic, not the number
of hourly statistics series. Long-term statistics survive normal history
purges. Use the [Recorder profiles](recorder.md) to reduce new detail history
and statistics while retaining total energy for the Energy dashboard.

## Getting help

Include the integration and Home Assistant versions, wallbox model/firmware,
control mode, expected behavior and the exact error. Download integration
**diagnostics** from its entry menu. Identifying register fields, the wallbox
serial number and full serial-port path are omitted from that download.
Review any additional logs or command output before posting; remove serial
numbers, private hostnames and adapter identifiers.
