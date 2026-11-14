# The app's web pages

[User guide](README.md) · [Dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Open the service app and select **Open Web UI**. The pages are for Home
Assistant administrators and open through Home Assistant itself; no port or
token is needed in the browser. They need **Supervisor 2026.10.4 or newer**.

The bar at the top has four groups; **Charging**, **Settings** and **Diagnostics** show their
pages in a second row.

| Group | Page | What it is for |
|---|---|---|
| **Overview** | [Overview](#overview) | What the wallbox and the app are doing right now. Read-only. |
| **Charging** | [Charge target](#charge-target) | Limit kWh or the selected vehicle percentage without starting charging. |
| | [Plan departure](#plan-departure) | One trip with a target percentage and automatic start/current planning. |
| | [Guest charging](#guest-charging) | A quick, limited order for a visitor's car. |
| **Settings** | [Connection](#connection) | USB or LAN connection, optional LAN-adapter setup. |
| | [Runtime](#runtime-settings) | Polling intervals and control limits, with a preview before saving. |
| **Diagnostics** | [Registers](#registers) | Every wallbox register with its raw and decoded value. Read-only. |
| | [Log](#log) | The Modbus traffic as it happens, for troubleshooting. |

Every page follows the browser language in English, German, French, Dutch and
Italian, and every page has the same navigation bar.

Everyday charging stays in Home Assistant: the current slider, **Charging
paused**, targets and departure planning. The app pages add a view of the
service and orders that keep running while Home Assistant is offline.
The orders can also be started and stopped from a Home Assistant dashboard or
automation; see [Use the app from Home Assistant](app-from-home-assistant.md).

[Overview](#overview) · [Charge target](#charge-target) · [Plan departure](#plan-departure) · [Guests](#guest-charging) · [Settings](#runtime-settings) · [Diagnostics](#log)

## Overview

The overview opens first. It refreshes every five seconds while the page is
visible and shows:

- **My charging**: which of the app's controllers decides right now (a
  guest charge, departure plan or manual control), why
  the car does not charge yet, the next step with its time, the target and
  whether Home Assistant confirmed the car. Home Assistant shows the same
  summary as [three sensors](entities.md#charging-control-at-a-glance).
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

The command history is collapsed by default. Open it when a command did not
have the expected result. The overview also gives a suggested action for common
waiting states; it does not override a pause or authorize a vehicle.

## Charge target

Open **Charging → Charge target**. Set additional wallbox kWh or the selected
vehicle's percentage target and explicitly save. These are the same values as
HA's native target entities. A target only limits charging; it never starts or
resumes a pause. 0 disables the respective limit. If both are active, both apply.

Set up vehicle sources and the optional maximum in HA first. The app respects
that maximum and refuses a new percentage target when a required cap is unknown.
It does not invent a vehicle SoC or battery capacity.

## Plan departure

Open **Charging → Plan departure** after enabling the vehicle's departure
planning in HA and providing usable battery capacity. Set the percentage target,
then select one dated departure and save. HA and the app show the same trip.
Calendar and vehicle timer imports remain optional HA buttons; imports replace
this same departure rather than creating another schedule.

An active plan may lift a manual pause. Clearing the departure cancels the plan,
keeps the ordinary targets and may end a pause owned by the plan. Check the
planning status; the requested target time is not a guarantee. See
[the three-mode guide](app-from-home-assistant.md) and
[departure planning](departure-planning.md) for examples and restart behavior.

## Guest charging

**Guest charging** (German: **Gastladen**) starts one order right away for a
visitor's car, without any vehicle data.

1. Let the guest authorize locally first, for example with an RFID card, so the
   wallbox's charging release is on.
2. Open **Guest charging** and enter the **additional energy**, the **maximum
   duration** (1 to 1440 minutes) and the **current per phase**.
3. The fields share a saved guest draft with HA. **Save draft** stores edits without starting; **Reload saved draft** adopts changes from another window.
4. Select **Preview**, check the start and end times and select **Confirm and
   start when permitted** within one minute.

A guest order uses no battery level and no saved vehicle target. It cannot be
created while a battery target is active in Home Assistant; set that target to 0
first. If a battery target is switched on later, the guest order is interrupted.

A guest session stops at the energy
amount or the end time, ends when the car is unplugged, and **Clear stopped
order without resuming** never restarts charging.

## Registers

**Registers**, a page below **Diagnostics**, lists every register the integration knows, with address, raw
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
restart with a lower cap. A queued command, a guest session and Home Assistant's
departure plan use the new cap when they next set the current.

A value changed in the app's **Configuration** tab is only read at the next app
restart. Until then the page shows it as saved but not yet effective; **Change
preview** and **Confirm and save** apply it at once. A changed connection, read-only mode,
listen address or port always needs an app restart; the page lists them.

If someone changed the settings meanwhile, the page asks you to reload. If a
save could not be confirmed, the page blocks further saves until the app
restarts; check the **Configuration** tab to see what was saved.

## Connection

**Connection** (German: **Verbindung**) holds the USB or LAN connection and the
optional LAN-adapter setup; see [first setup](modbus-service.md#3-configure-and-start-read-only)
and [LAN adapters](lan-adapters.md#service-app). Changes take effect after an
app restart.

## Log

**Diagnostics → Log** (German: **Diagnose → Protokoll**) shows the app's own Modbus reads, writes and
heartbeats one line each, as they happen. It starts when you open the page and
follows the newest line; scroll up to read on undisturbed. **Pause** holds the
view while new lines are still collected, **Filter** narrows it to a register
or result, **Download** saves the lines shown as a text file.

The recording lives in memory only, adds no bus traffic and hides serial
numbers and login traffic. It ends by itself within ten minutes after the page
is closed. See [troubleshooting](troubleshooting.md#watch-app-modbus-communication-live).

## Limits

- The pages show what the app knows. Home Assistant supplies vehicle data and departure choices. The overview
  shows the app’s active controller and target; edit the vehicle profile and
  next departure in HA.
- An app restart pauses a running order. It continues only when the wallbox
  proves the same plug-in; otherwise the app hands control back to Home
  Assistant.
- A stop you request is shown as requested until the wallbox confirms it. Check
  the overview and its command results.
- The browser never repeats an order by itself. If a reply is lost, reload the
  page and check the current state before trying again.

For the HTTP interface behind these pages, see the
[service reference](service-reference.md#guest-charging-api).
