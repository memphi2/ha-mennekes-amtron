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
| [Charging jobs](#charging-jobs) | A charging order the app runs on its own, with optional early start and an estimated finish. |
| [Guest charging](#guest-charging) | A quick, limited order for a visitor's car. |
| [Weekly plans](#weekly-plans) | Recurring departures for one car, turned into charging jobs for up to seven days. |

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
   current cap. Optionally enter an [immediate reserve](#immediate-reserve).
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

### Finish estimate

While an order charges, the page estimates when its energy amount will be
reached: a time range, the measured power and whether the range fits the window
(**Within the accepted time window**, **Time window may be too short** or
**Energy target unlikely within the time window**). The estimate comes from the
wallbox meter, not from the battery level; the car or another limit may stop
earlier. It never changes the order.

- It needs at least three valid measuring intervals of 30 to 180 seconds with
  unchanged current and phases. Until then the page says it is collecting
  intervals. A pause, a changed current or phase setting, a meter gap or a
  meter reset starts again.
- The range spans the slowest and fastest measured power with a 15 % margin.
  It is not a guarantee: tapering near a full battery, temperature or other
  limits can change it. If the power varies too much or the end lies more than
  seven days ahead, no range is shown.
- The range includes the immediate reserve, a boost and the wait for the
  planned start, without counting energy twice.
- While an order waits for its window, the range can come from earlier
  measurements of the same car, current and phases within the last 30 days. The
  car is the vehicle selected in Home Assistant or the weekly plan's profile.
  Guest orders and orders without a selected vehicle learn only for the
  current charge.

While an order waits for proof of the same plug-in
([after an app restart or a connection loss](#after-an-app-restart-or-a-connection-loss)),
no estimate is shown. If the learned values cannot be saved, the page says so;
charging is not affected.

### Immediate reserve

An **immediate reserve** charges part of the energy amount right away, then
pauses until the planned start. Example: with 20 kWh, a reserve of 5 kWh and a
start tonight, the app charges 5 kWh now and the remaining 15 kWh in the window.
The reserve is part of the amount, not extra, and 0 switches it off.

The reserve is energy measured by the wallbox meter, not a battery percentage
or driving range. It never overrides a stop, a switched-off release or a reached
target. Once reached, it stays reached, also across an app restart. Guest
orders have no reserve.

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

### After an app restart or a connection loss

When the app restarts or the wallbox is unreadable for more than a minute, the
order pauses charging and shows **Interrupted; checking whether the same plug-in
continues**. It continues by itself only when the wallbox proves that the car
stayed plugged in meanwhile:

- the wallbox's session duration kept running with the clock (within two
  minutes)
- its session counter is unchanged and its session energy did not start over
- the car is still plugged in and the same vehicle is selected in Home Assistant

If any of this is missing or does not hold within a minute of the wallbox being
readable again, the order is interrupted as below. A wallbox without a session
counter (register layout before v01.02) cannot prove it; there the order is
always interrupted.

### When an order is interrupted

The app ends an order and does not restart it by itself when:

- the car is unplugged; an order never carries over to the next car
- the same plug-in could not be proven after an app restart or a connection loss
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

## Weekly plans

**Weekly plans** (German: **Wochenpläne**) save a recurring schedule for one
car: weekdays, departure time, charging window in minutes, current, energy per
departure and an optional [immediate reserve](#immediate-reserve). Up to 16
plans are stored, one per vehicle profile.

1. Enter a **vehicle profile reference**, for example `bmw-i4`. It is a label
   you choose; the app does not recognize the car by it.
2. Choose the time zone, weekdays and the other values. **Date exceptions**
   skip a day or move its departure, one line per date: `2026-12-24 = skip` or
   `2026-12-25 = 09:00`.
3. Select **Preview changes**, check the listed dates and select **Save this
   recipe**. Saving alone never charges.
4. With the car plugged in, select the saved plan, confirm **I have checked that
   the selected vehicle is connected**, select **Preview activation** and
   **Confirm activation** within one minute.

The activation creates a charging job for the next departure right away, so an
immediate reserve charges at once. When that job ends, control returns to Home
Assistant as for any finished order: PV or other automations may charge until
the next departure's window opens. Then the app creates the next job, which
takes over charging, also from a car Home Assistant is charging. It waits while
the meter is not current, a command is still queued, the release is off or
another limit requires a stop. Later departures therefore have no separate
reserve.

Between departures, pausing, switching off the release or locking in Home
Assistant does not end the plan; it only keeps the next job from starting while
it lasts. A new order on **Charging jobs** or **Guest charging** ends the plan.

An activation covers only this connection and ends after seven days at the
latest. Unplugging, an app restart, a wallbox unreadable for more than a minute
between departures, a stop or clear on **Charging jobs**, editing or removing
the active plan, **Stop weekly automation** or another limit that stops a
running job all end it; it never restarts by itself. A running job of the plan
continues after an app restart only as described under
[After an app restart](#after-an-app-restart-or-a-connection-loss), and then
without the plan. Each departure
date runs at most once, also after an edit or a restart. A local time that does
not exist on a clock change is shown as invalid instead of being moved; for a
repeated time, choose the earlier or later occurrence.

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
(in use now), **Saved** and your proposed change. Select **Change preview**,
check the result and select **Confirm and save**.

| Setting | When it takes effect |
|---|---|
| Status interval, measurement interval | At the next polling cycle. |
| Service current cap | At once for new commands. A charge running above a lower cap is lowered after the next poll. |
| State-change interval | At once, also for a start or stop that already waits. |
| Phase-switch grace | For the next phase-switch wait; a wait that already runs keeps its length. |

None of them needs a restart, and the page never restarts the app. The app
lowers a charging current above its cap after the next poll, also after an app
restart with a lower cap. A queued command, a charging job and Home Assistant's
departure plan use the new cap when they next set the current.

A value changed in the app's **Configuration** tab is only read at the next app
restart. Until then the page shows it as saved but not yet effective; **Change
preview** and **Confirm and save** apply it at once. A changed connection, read-only mode,
listen address or port always needs an app restart; the page lists them.

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
- An app restart pauses a running order. It continues only when the wallbox
  proves the same plug-in; otherwise the app hands control back to Home
  Assistant.
- A stop you request is shown as requested until the wallbox confirms it. Check
  the overview and its command results.
- The browser never repeats an order by itself. If a reply is lost, reload the
  page and check the current state before trying again.

For the HTTP interface behind these pages, see the
[service reference](service-reference.md#charging-job-api).
