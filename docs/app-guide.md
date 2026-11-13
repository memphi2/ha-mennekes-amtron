# The app's web pages

Open the service app and select **Open Web UI**. The pages are for Home
Assistant administrators and open through Home Assistant itself; no port or
token is needed in the browser. They need **Supervisor 2026.10.4 or newer**.

| Page | What it is for |
|---|---|
| [Overview](#overview) | What the wallbox and the app are doing right now. Read-only. |
| [Connection](#connection-and-debug-log) | USB or LAN connection, optional LAN-adapter setup. |
| [Debug log](#connection-and-debug-log) | A short live recording of the Modbus traffic for troubleshooting. |
| [Registers](#registers) | Every wallbox register with its raw and decoded value. Read-only. |
| [Runtime](#runtime-settings) | Polling intervals and control limits, with a preview before saving. |
| [Charging jobs](#charging-jobs) | A charging order the app runs on its own, with optional early start. |
| [Guest charging](#guest-charging) | A quick, limited order for a visitor's car. |

Every page follows the browser language in English, German, French, Dutch and
Italian, and every page has the same navigation bar.

Everyday charging stays in Home Assistant: the current slider, **Charging
paused**, targets and departure planning. The app pages add a view of the
service and orders that keep running while Home Assistant is offline.

## Overview

The overview opens first. It refreshes every five seconds while the page is
visible and shows:

- the **observed charging state** read from the wallbox, and why the app holds
  a pause or a stop, for example a reached target or a switched-off release
- power, session energy and the current limit read back from the wallbox;
  **0 A means unlimited**, not paused
- whether writes are enabled and whether the heartbeat is confirmed
- the **next queued action** and its earliest time; queued does not mean done
- the saved energy and battery targets; they are limits, not progress
- up to 32 recent command results since the app started, newest first

If the app cannot be reached, the page hides old values instead of showing them
as current. The overview never changes charging.

## Charging jobs

A charging job is one order for the car that is plugged in now: a time window,
a current per phase and a maximum amount of additional energy. The app runs it
by itself, so it continues while Home Assistant restarts or is offline.

### Before you start

The order can only start when all of this is true:

- a car is plugged in and the wallbox reports it
- **Charging release** is on and the wallbox is not locked or faulted
- writes are enabled (app **Read-only** off)
- the energy meter reports current values

A job never switches the release on, unlocks the wallbox, authorizes RFID or
Virtual Plug & Charge, changes the installation's limits or switches phases.
Energy and battery targets you set in Home Assistant keep working: when one is
reached, it stops the order.

### Create an order

1. Open **Charging jobs** (German: **Ladeaufträge**).
2. Enter **Start**, **Finish no later than**, the **additional energy** in kWh
   and the **current per phase** in A. The current cannot exceed the app's
   current cap.
3. Select **Preview**, check the summary and select **Confirm this order**.

Confirm soon after the preview. If the start time is already more than a minute
in the past, the app asks for a new preview.

### While the order runs

| Time | What the app does |
|---|---|
| Before the start | Keeps charging paused. |
| In the window | Charges with the chosen current until the energy amount is reached. |
| Energy reached or window ended | Pauses charging. Once the wallbox confirms the pause, Home Assistant controls charging again. |

A finished order never restarts charging by itself. Charging stays paused until
you resume it in Home Assistant or an automation does. The finished order stays
on the page until you clear it or create a new one.

While an order runs, Home Assistant cannot change the current, resume charging,
switch phases or start PV or departure charging. It reports that a charging job
of the app controls charging. These Home Assistant actions **stop** the order:
**Charging paused** on, **Charging release** off, and locking the wallbox.

The meter is read at the normal polling interval, so a little more energy than
the set amount can flow before the pause takes effect. The order is a limit, not
a guarantee that the car receives this amount or finishes by the end time.

### Early start (boost)

Before the window starts, **Temporary early charging** can charge early for 1 to
120 minutes. Enter the minutes, select **Preview boost** and confirm. The boost
ends at the planned start at the latest; the normal window then takes over.

The boost uses the same current and the same energy amount. Energy charged early
is deducted from the amount left for the window. **Return to plan now** ends only
the boost. **Stop automation for this connection** ends the whole order.

### Stop and clear

- **Stop automation for this connection** stops the order and pauses charging.
  Once the wallbox confirms the pause, Home Assistant controls charging again.
- **Clear stopped order without resuming** removes a stopped or finished order
  from the page. It never starts charging. While the pause is not yet
  confirmed, clearing needs a confirmed pause or an unplugged car.

### When an order is interrupted

The app ends an order and does not restart it by itself when:

- the car is unplugged; an order never carries over to the next car
- the wallbox or the plug state is unreadable for more than a minute
- the app restarts
- another limit requires a stop, for example a reached target
- the clock jumps backwards or the meter is reset

An interrupted order pauses charging like a finished one and then hands control
back to Home Assistant. A short gap in meter readings only pauses the order; it
continues once readings return. To let the app charge again, create a new order.

The order belongs to the physical connection, not to a particular car. The app
does not identify vehicles; it only knows that the plug stayed connected.

## Guest charging

**Guest charging** (German: **Gastladen**) starts one order right away for a
visitor's car, without any vehicle data.

1. Let the guest authorize locally first, for example with an RFID card, so the
   wallbox's charging release is on.
2. Open **Guest charging** and enter the **additional energy**, the **maximum
   duration** (1 to 1440 minutes) and the **current per phase**.
3. Select **Preview**, check the start and end times and select **Confirm and
   start when permitted** within one minute.

A guest order uses no battery level and no saved vehicle target. It cannot be
created while a battery target is active in Home Assistant; set that target to 0
first. If a battery target is switched on later, the guest order is interrupted.

Otherwise a guest order behaves like a charging job: it stops at the energy
amount or the end time, ends when the car is unplugged, and **Clear stopped
order without resuming** never restarts charging.

## Registers

**Registers** lists every register the integration knows, with address, raw
words, decoded value, last successful read and last attempt. The **Quality**
column describes the reading, not the wallbox:

| Quality | Meaning |
|---|---|
| OK | Read successfully within its normal interval. A fault code can be OK when it was read correctly. |
| Degraded | The last value is older than expected, a later read failed or the value is unknown. |
| Error | No usable value, or the wallbox is not reachable. |
| Unknown, unsupported, write-only | Not read yet, not supported by this wallbox's firmware, or not readable. |

Filter, sort and **Freeze display** only affect the browser. The page adds no
Modbus traffic and no Recorder history. The serial number is hidden.

## Runtime settings

**Runtime** (German: **Betrieb**) shows each setting three times: **Effective**
(in use now), **Saved** and your proposed change. Select **Preview**, check the
result and select **Confirm and save**.

| Setting | When it takes effect |
|---|---|
| Status interval, measurement interval | At the next polling cycle, without a restart. |
| Service current cap, state-change interval, phase-switch grace | After the next app restart. |

A lower current cap is not effective until the app restarts; until then the
effective value stays in force. The page never restarts the app itself. It also
shows changes saved in the app's **Configuration** tab that still wait for a
restart.

If someone changed the settings meanwhile, the page asks you to reload. If a
save could not be confirmed, the page blocks further saves until the app
restarts; check the **Configuration** tab to see what was saved.

## Connection and Debug log

**Connection** (German: **Verbindung**) holds the USB or LAN connection and the
optional LAN-adapter setup; see [first setup](modbus-service.md#3-configure-and-start-read-only)
and [LAN adapters](lan-adapters.md#service-app). Changes take effect after an
app restart.

**Debug log** records the app's own Modbus reads and writes for up to 10 minutes
and at most 200 entries, in memory only. It stops and erases itself, adds no
extra bus traffic and hides serial numbers and login traffic. See
[troubleshooting](troubleshooting.md).

## Limits

- The pages show what the app knows. Vehicle identity, current battery level and
  the departure plan live in Home Assistant and are not shown here.
- An app restart interrupts a running order. The app pauses charging after the
  restart and then hands control back to Home Assistant.
- A stop you request is shown as requested until the wallbox confirms it. Check
  the overview and its command results.
- The browser never repeats an order by itself. If a reply is lost, reload the
  page and check the current state before trying again.

For the HTTP interface behind these pages, see the
[service reference](service-reference.md#charging-job-api).
