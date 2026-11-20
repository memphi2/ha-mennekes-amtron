# Everyday charging

[Guide home](README.md) · [First charge](quickstart.md) · [Dashboard YAML](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Open the wallbox under **Settings → Devices & services → Mennekes Amtron RTU**,
or use your dashboard. The names below follow the English interface; HA and the
app also provide German, French, Dutch and Italian labels.

## Choose what you want to do

| You want to… | Do this |
|---|---|
| Charge normally | Choose a current, enable **Charging release** and turn **Charging paused** off. |
| Stop for now | Turn **Charging paused** on; wait for confirmation. |
| Add 12 kWh and stop | Set **Energy target** to 12; start or resume separately. |
| Charge a visitor's car | Optionally set an energy target, cost target or target time, then press **Start guest charging**. |
| Stop your car at 80% | Set up its optional battery source, then set **Vehicle charge target** to 80. |
| Have your car ready for a trip | Enable [vehicle planning](departure-planning.md), choose a percentage and one departure time. |

A limit is a limit: saving it does not start charging. There is no separate
charging order to create or save.

## Everyday controls

- **Charging current limit:** choose 6 A or more, up to the available maximum.
  The vehicle can draw less than this offer.
- **Charging paused:** on requests a pause; off resumes at the remembered current.
- **Charging release:** permission from the wallbox controller. Use pause for
  everyday stops rather than repeatedly changing release.
- **Wallbox locked:** a separate block. Guest start does not unlock the wallbox.

**0 A removes the software current limit; it does not stop charging.** It also
bypasses the integration/app current caps. The wallbox's installation limits
still apply. Changing the current slider while paused can resume charging.

An enabled departure plan can lift a manual pause. To keep a planned vehicle
stopped, clear its departure time first, then pause. A paused guest is excluded
from vehicle planning until unplugging.

## Shared targets and target time

| Setting | Guest | Selected vehicle |
|---|---|---|
| **Energy target** | Additional wallbox kWh from when the positive target was saved | Same |
| **Cost target** | An amount in Home Assistant's currency from when it was saved; it sets the energy target | Same |
| **Target time** | Ends the charge at this time; start is immediate | With planning enabled: be ready for the next trip |
| **Vehicle charge target** | Ignored; its saved value remains | Stop at the reported battery percentage |

Energy and cost values are estimates from an uncalibrated meter reading (no
MID or Eichrecht conformity) and must not be used to bill others.

For guests, whichever of energy target, cost target and target time is reached
first ends the charge. With none set, no automatic limit is added. Set the
energy, cost or vehicle charge target to **0** to disable it. Clear the time separately; in the Mushroom example, hold the time chip.

Saving a positive kWh value starts a fresh baseline. Guest start uses that
baseline, including energy already delivered since it was set. It does not
restart the counter. Change or disable an already reached goal before resuming.
Vehicle-profile changes cannot replace an active guest's target time; edit it
explicitly in HA or the app. There is one target time, and the wallbox service
keeps it: a time changed while a guest charges stays as changed afterwards, and
a time can only be set while the service is reachable.

## Guest charging

**Requires write access.** No vehicle profile is needed.

1. Plug in the car.
2. Optionally set **Energy target**, **Cost target** and/or a future **Target time**. In the app,
   save each changed field before starting.
3. Press **Start guest charging**. This requests release and start together,
   lifts a manual pause and grants this plug-in permission even with virtual
   Plug & Charge enabled. A hardware write interval may still delay execution.
4. Check **Total power** and **Charging wait reason**. **Guest charging** is on
   while the guest charges. To stop early, use the normal pause switch, the
   app's **Pause guest charging** button, or unplug.

The guest uses the normal current setting. Its vehicle percentage target and
vehicle plan are ignored. Locks, faults, local RFID authorization and electrical
limits still apply. Unplugging ends permission; a waiting guest start must not
release another car.

With the app, an HA Core restart leaves the app running; a direct entry's
service restarts with HA. After a service restart, guest permission and limits
are restored only when wallbox readings establish the same plug-in. A
deliberate pause stays paused. Without sufficient evidence, or if the saved
time is inconsistent, the service stops charging and needs a new guest start. Check the
limits first. There is no guest-history screen or saved guest-order editor.

## Where do I enable controls?

| Connection | Enable writes here |
|---|---|
| Service app | App **Configuration → Read-only** off; save and restart the app |
| Direct USB/LAN | Integration **Configure → Wallbox → Current limit and polling → Control mode: Modbus master** |

With the app, HA follows the app's permission; there is no second write
switch. Read-only disables commands and heartbeat, but still polls the bus.
Only one owner may use the adapter. Check readings before enabling writes.

## Understand the activity

In Home Assistant **Activity**, filter by the **Activity** entity to follow
control commands and physical charging changes together. The entity's `trigger`
attribute explains why a command ran; the subsequent wallbox change tells you whether charging
started or stopped. See [activity details](troubleshooting.md#understand-the-activity-timeline).

## Understand the readings

**Total power** is actual charging power; **Signaled current** is what the wallbox
offers. **Session energy** counts the current session; **Total energy** is the
lifetime counter. **Vehicle connected** alone does not mean charging is allowed.

For a waiting charge, start with **Charging status** and **Charging wait
reason**; its attributes show a queued change and the active limits. **Connection quality** includes the `heartbeat` attribute (`ok` or
`problem`). [All entities and their meanings](entities.md).

The charging status distinguishes **Checking status** (briefly differing readings),
**State is ambiguous** (still differing after two polls and at least ten seconds)
and **Waiting for readings** (required values missing). A confirmed fault is
shown immediately. [What these messages mean](troubleshooting.md#charging-status-is-being-checked).
The app and Mushroom examples show **Connection interrupted** for lost
communication; native HA entities remain **Unavailable**.

## Waiting between charging changes

The default state-change wait is **300 seconds**, configurable from 0–3600.
Current-register writes always keep a **five-second minimum interval**. A fresh,
confirmed absence of a car can remove the longer wait; unknown data cannot.
Reached energy/time limits bypass the longer wait but retain the write floor.

A pending action is accepted, not completed. Wait for its due time and readback.
Do not restart the app to force it. MENNEKES recommends more than five minutes;
use, for example, 360 seconds if you want to exceed that recommendation.

## Cancel a queued change

Use **Cancel pending change** to remove an unsent request. This does not reverse
a command already applied. The latest ordinary current request replaces an
older waiting current request. [A change did not run](troubleshooting.md#the-charging-current-does-not-follow).

## Configuration and options

Connection settings belong to the connection owner: **Reconfigure** for a direct
entry, or the app's **Connection** page. App current caps, waits and polling live
under **Settings → Runtime**. Configure vehicle profiles, costs and optional
planning in HA. [Settings map and restart requirements](installation.md#change-settings-later).

## Energy target

A positive value limits additional energy measured at the wallbox; it is not
battery energy. The goal and baseline survive a restart. A missing or reset
meter causes a protective pause rather than a claim that the target was reached.

A temporary meter outage can resume only a pause caused by that outage, with the
same goal and no intervening manual command. A reset counter needs a new goal;
a reached goal stays stopped while the car is plugged in. After an owner
restart, resume explicitly.

A reached energy or cost target belongs to the charge that reached it. Once
the wallbox confirms that the car was unplugged, the target is cleared and the
pause ends, whoever set it, so the next car charges without the previous limit. A
target that is not reached yet stays for the next plug-in.
[Target troubleshooting](troubleshooting.md#targets-and-departure-planning).

## Cost target

With charging costs configured, **Cost target** limits a charge by an amount
of money instead of energy. The service keeps the amount together with the
**Energy target** that carries it: at the price that applies now it arms the
energy the amount buys. The app's **Charging** page can set it, too, at the
last price Home Assistant reported. Home Assistant tells it each new price, at most once
a minute and only for a change of at least 0.1 ct/kWh; the service then books
the energy so far at the old price and re-arms the rest at the new one. It
therefore stops the charge on its own; while Home Assistant is down it keeps
the last price. With a dynamic price the amount is kept to within one 0.1 kWh
step per price change, and it rests on the same estimate as the charging
costs — it is not for billing.

Setting **Energy target** by hand ends the cost target, and setting the cost
target to zero clears both. Like the energy target it never starts or resumes
a charge. Without a current price a new amount is refused, and an armed one
keeps its last price.

## Phase switching

If the wallbox supports it, select single phase or all available phases. During
charging, the controller first requests a pause, waits for confirmation and the
configured grace, then switches. Failed confirmation leaves the charge paused.
Wait for completion, then resume explicitly. [Phase-switch help](troubleshooting.md#phase-switching-leaves-charging-paused).

## Heartbeat and Home Assistant restarts

| What happens? | Expected behavior |
|---|---|
| HA Core restarts, app keeps running | App heartbeat, accepted limits and handed-over vehicle planning continue |
| HA restarts with a direct USB/LAN entry | The service inside HA stops with it; heartbeat stops and wallbox fallback applies until HA is back |
| App, host or adapter fails/restarts | Heartbeat can stop; wallbox fallback applies |
| App starts again | Ordinary intent/limits are loaded; guest permission needs same-plug-in evidence |
| HA loses only its link to the app | HA controls are unavailable; this alone does not prove the app heartbeat stopped |

A restart of the service (the app, or HA for a direct entry) cancels unsent
queued commands. A restart is not permission to
undo a deliberate pause. [Configure fallback](safety.md#the-heartbeat-and-what-happens-without-it)
and use [Repairs help](troubleshooting.md#heartbeat-or-service-connection-repair) when a fault remains.

## Energy dashboard and database size

Add **Total energy** to the HA Energy dashboard. This is monitoring, not a billing
meter. Use [Recorder settings](recorder.md) to keep useful energy history while
excluding noisy diagnostic values. No per-heartbeat history is required.

## Vehicle profiles

A vehicle integration is optional. [Vehicle setup](vehicle-setup.md) explains one
or several profiles and the sources each feature needs.

### Optional automatic vehicle selection

Cable and home signals help select a unique vehicle. Manual selection still
works. Selection supplies data; it is not charging permission.

### Optional Virtual Plug & Charge

The separate wallbox switch requires fresh evidence before automatic permission.
Selecting a profile manually is not recognition. A fresh recognized plug-in may
lift a previous pause; locks and reached limits still apply.
[Permission rules](vehicle-setup.md#optional-virtual-plug--charge).

## Optional vehicle charge target

Choose the car's battery percentage source, then set its target. The car's own
reported maximum is a separate optional source. A missing SoC leaves charging
unchanged; it does not invent a battery reading. [Set up percentage limits](vehicle-setup.md#optional-vehicle-charge-target).

## Charge for the next departure

Choose a percentage, usable battery capacity and one dated departure. Import a
calendar event or vehicle timer if wanted. Planning adapts to measured power and
can resume charging; a guest remains excluded. [Plan a trip](departure-planning.md).

## PV charging and schedules

Use the [PV blueprint](blueprints.md#set-up-pv-charging) with your surplus sensor.
Avoid competing current controllers: choose which automation or vehicle plan
owns the charging current.

## Actions

Use native entities for daily operation. For automations, see [actions and events](automations.md)
and [app actions](app-from-home-assistant.md#automation-actions).

`mennekes_amtron_rtu.set_charging_current` takes these fields:

| Field | Meaning |
|---|---|
| `device_id` | The wallbox device, or a list of them. Required. |
| `current` | The limit in ampere: 6 to 32, or 0 with `allow_unlimited`. A value above the wallbox's own maximum or the configured cap is lowered to it; 0 removes the limit, the cap included. Required. |
| `allow_unlimited` | Must be `true` to send `current: 0`, which removes the limit. It is not a pause. |

```yaml
action: mennekes_amtron_rtu.set_charging_current
data:
  device_id: 0123456789abcdef0123456789abcdef
  current: 10
```
