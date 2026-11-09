# Charge for your next departure

[Documentation](README.md) › Departure planning

Choose a target percentage and one departure time. The integration starts
charging as late as it safely can, charges at full current and reduces to 6 A
toward the end, so the car reaches the target at departure. This is optional;
ordinary percentage and kWh targets work without a departure plan.

**A departure plan may lift a manual charging pause.** Clear **Next departure**
when you want the vehicle to stay paused. A percentage target on its own never
resumes charging.

Departure planning, calendar import and BavarianData timer import are available
in stable 0.6.1. Shared [vehicle profiles](vehicle-setup.md) and the menu layout
below are new in **beta 0.8.0b1**; existing separate vehicle sources continue to
work. In service mode, use app/service 0.6.1 or newer.

## Example: ready tomorrow at 07:30

1. Configure the car's [charge level](vehicle-setup.md) and set **Vehicle charge
   target** to the percentage you want, for example **80%**.
2. Enable departure planning and provide **usable** battery capacity. Choose
   a fixed value or a suitable HA entity; a configured BavarianData profile can
   supply it when that source is available.
3. Set **Next departure** to tomorrow's full date and **07:30**, or copy that
   trip from a calendar or vehicle timer.
4. Check the displayed date, time, target and planning status. From
   **0.8.0b6**, **Planned charging start** shows when charging starts, or
   started, for this trip. Unlock and authorize the car as required. The plan cannot bypass those restrictions.
5. To add climate, install the [preconditioning blueprint](blueprints.md#start-vehicle-climate-before-departure)
   and choose the HA action and lead time you want.

Manual time, calendar and vehicle import can all remain available. You choose
which one supplies the next trip; importing a recurring vehicle timer still
creates only one HA departure plan.

## Enable planning

Set up a vehicle charge-level source and a positive **Vehicle charge target**
first. Open the profile under **Configure → Vehicles**, then **Next departure → Configure departure planning** and enable departure planning. Choose
a fixed **usable battery capacity** in kWh, a suitable value from the active
vehicle profile, or an entity that reports it in kWh or Wh. Only the chosen
capacity input appears. The integration never guesses
it from the nominal model capacity.

## Choose a time for this trip

Manual entry is available once planning is enabled. Calendar, dated entity
and vehicle-provider inputs are separate optional choices; select only the
ones you want to use. They do not require each other.

Set **Next departure** to a full date and time. This is a one-trip target, not
a repeating daily plan. Manual entry, calendar selection and optional vehicle timer
selection can coexist; choose the source you want for each trip.

### Calendar

For a calendar, enable the calendar option in **Next departure**, then select a
**Departure calendar** and enter the **exact event title**. Press **Use next
calendar event** to copy the first
matching timed event within the next 14 days into **Next departure**. All-day
events are ignored. Calendar changes after selection do not alter the copied
time; press the button again to import the updated event. The
[calendar blueprint](blueprints.md#plan-the-next-trip-from-a-calendar) can
perform this selection ahead of the next trip using a Toggle helper.

### BavarianData climate timer

To use the active BavarianData profile's timers, enable **Use vehicle departure
timers** in **Next departure**. You do not need to select the integration and car
again. Use **Use vehicle departure** when you want to copy the next active climate
timer. Existing setups without profiles retain their separate vehicle selection.
Other vehicle integrations can supply the generic SoC, capacity and dated
departure entities described here.
The adapter reads the vehicle's last locally
published Home Assistant states; it does not fetch BMW cloud data or change the
vehicle's timers. Those states may be cached, so check **Next departure** after
importing, especially after changing the timer in your car or the BMW app.

- BMW streams the timers only when they are ticked in the car's data selection.
  BavarianData's **Choose streamed data** does not tick them, even with the
  *Metadata* cluster selected. In the BMW portal, open the car's **BMW CarData**
  page, choose **Change data selection** under **CarData Stream**, and tick the
  action, hour and minute fields of each climate timer you want to use (German
  portal: *Aktion/Stunde/Minute für Überschreiben der Klimazeitautomatik* for
  the one-off timer, *… Zeitautomatik 1/2 an Wochentagen* for the weekly ones).
  Save, then change the timer once in the car or the MyBMW app: the sensors
  only appear after the car has sent their values.
- Enable the **action**, **hour** and **minute** sensor for each timer you want
  to use in the BavarianData device's entity list. These diagnostic entities
  start disabled. The timer's action must report `activate`; missing or
  unavailable values are ignored.
- The one-off timer (`overwriteTimer`) supplies a clock time, without a date.
  The import uses its next occurrence within the coming 24 hours.
- For recurring timer 1 or 2, select the timer's weekdays
  to match the days configured in your vehicle. BavarianData does
  not expose those days. Leave a timer's day selection empty to ignore it.
- **Vehicle time zone** defaults to Home Assistant's time zone. Override it
  when the vehicle's timer uses another zone, for example `Europe/Berlin`.
  Nonexistent clock times during a daylight-saving change are skipped.

The vehicle departure button selects the earliest usable upcoming occurrence once. It does
not turn a recurring vehicle timer into a recurring HA charging plan. A copied
vehicle or calendar time stays selected until you replace or clear it.

### An existing departure-time entity

A vehicle integration that already supplies a full dated timestamp can instead
be selected by enabling the optional **Departure time entity** source. An `input_datetime` with both date
and time also works. This optional source can automatically select a new future
timestamp. A manual, calendar or vehicle selection takes priority over the source's
current timestamp; it can be replaced when that timestamp changes to a new
trip. A different time-zone representation of the same instant is still the
same trip and does not reactivate a cleared plan. **Clear departure time** cancels the selected plan without changing the
percentage ceiling or immediately changing the wallbox.

## Change or cancel a trip

Disabling **Next departure** clears its selected trip and cancels commands queued
by that plan. It keeps the ordinary percentage target and the current pause
state. Existing configured sources are recognized after an update; the vehicle
departure button keeps its existing entity identity and automations.

## What the plan changes while charging

The planner uses the current vehicle SoC, remaining time, usable capacity,
known wallbox limits and phase information. From **0.8.0b7** it works in
three steps:

1. **Wait.** Charging stays paused until the energy still needed takes the
   remaining time at full current, plus a **30-minute reserve** for a charge
   slower than estimated. **Planned charging start** shows that time.
2. **Full current.** Charging runs at the wallbox's maximum current.
3. **Finish at 6 A.** Once the rest fits the remaining time at 6 A, the plan
   reduces to 6 A and lands on the target at departure instead of early.

Each SoC report recalculates this. A charge that is faster than estimated never
pauses again once it has started; it reaches the 6 A step sooner. A slower one
returns to full current. Before 0.8.0b7 the plan spread charging at a reduced
current and often started many hours earlier. **An active timed goal can lift even a
manually set charging pause** to meet the selected time. To prevent that,
clear **Next departure**. The ordinary percentage target without a departure
time still only limits charging and never resumes a pause.

With [Virtual Plug & Charge](vehicle-setup.md#optional-virtual-plug--charge)
(from **0.8.0b3**), plugging in a recognized car does not start charging ahead of
an armed plan. The release waits until the plan has paused until its start or
set its current.

## Climate before departure

The optional [calendar and preconditioning blueprints](blueprints.md) select
one trip and run your chosen vehicle action before departure. The vehicle
determines whether preconditioning draws grid or battery power; completed
charging targets still apply. Once the target is reached, the charging plan
cannot resume another charge. From **0.8.0b6**, raising **Vehicle charge
target** above the car's charge level plans the same trip again. Every target
change recalculates the plan. **Next departure** remains visible until that
time so the climate blueprint can still run, then clears automatically.
## Limits and missing data

The planner assumes 230 V per phase and initially 90% transfer efficiency.
The wallbox reports the car's phases only while it draws current. From
**0.8.0b7** the plan therefore remembers each vehicle profile's phases from its
last charge. A car never seen charging is planned with three phases. A one-phase
car then starts later than it should; its first charge corrects that from the
reserve, and every later trip plans with one phase.
Car limits, missing or delayed SoC reports, unknown vehicle power, the 6 A
minimum, the wallbox's configured transition delay and HA outages can make
the exact target time impossible. Missing SoC or capacity data leaves the
current charging state unchanged.

The plan needs HA running to recalculate current and start climate actions.
With the independent service, a queued timed-plan command expires at its
deadline even if HA is offline; it cannot resume charging later as an obsolete
request. Expiry does not undo a command already applied or stop an ongoing
charge. The service continues its heartbeat and last applied state, subject
to its charging limits. Service mode requires **app/service 0.6.1 or newer**
for this behavior; update it before the integration.

From **0.7.0b3**, the bus owner also checks for a disconnected vehicle before
applying a planned command. Confirmed unplugging cancels that command, including
while HA is offline when using service 0.7.0b3. Manual controls remain available.

Current adjustments require at least 1 A difference and are spaced by at least
five minutes; the status sensor has
stable states and adds no per-minute history entries.

[Vehicle setup](vehicle-setup.md) · [Calendar and climate blueprints](blueprints.md) · [Troubleshooting](troubleshooting.md#targets-and-departure-planning)
