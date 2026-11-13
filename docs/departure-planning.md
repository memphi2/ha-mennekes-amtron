# Charge for your next departure

[Documentation](README.md) › Departure planning

Choose a target percentage and one departure time. The integration starts
charging as late as it safely can, charges at full current and reduces to 6 A
toward the end, so the car reaches the target at departure. This is optional;
ordinary percentage and kWh targets work without a departure plan.

**A departure plan may lift a manual charging pause.** Clear **Next departure**
when you want the vehicle to stay paused. A percentage target on its own never
resumes charging.

The departure belongs to a car's [vehicle profile](vehicle-setup.md).

## Example: ready tomorrow at 07:30

1. Configure the car's [charge level](vehicle-setup.md) and set **Vehicle charge
   target** to the percentage you want, for example **80%**.
2. Enable departure planning and provide **usable** battery capacity. Choose
   a fixed value or a suitable HA entity; a configured BavarianData profile can
   supply it when that source is available. Optionally set a
   [minimum charge level](#minimum-charge-level) the car charges to at once.
3. Set **Next departure** to tomorrow's full date and **07:30**, or copy that
   trip from a calendar or vehicle timer.
4. Check the displayed date, time, target and planning status.
   The `planned_start` attribute of **Timed charging status** shows when
   charging starts, or started, for this trip. Unlock and authorize the car as required. The plan cannot bypass those restrictions.
5. To add climate, install the [preconditioning blueprint](blueprints.md#start-vehicle-climate-before-departure)
   and choose the HA action and lead time you want.

Manual time, calendar and vehicle import can all remain available. You choose
which one supplies the next trip; importing a recurring vehicle timer still
creates only one HA departure plan.

## Enable planning

Set up a vehicle charge-level source and a positive **Vehicle charge target**
first. Open the profile under **Configure → Vehicles**; after its name and charge level, the setup reaches **Next departure**. Choose **Configure departure planning** and enable departure planning. Choose
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

**Not every car sends its timers.** On an i4 and an i5, BMW CarData reported
only that a departure timer is set, not its time, even with the timer fields
ticked in the data selection ([BavarianData #308](https://github.com/JustChr/BavarianData/issues/308)).
**Use vehicle departure timers** is offered only when
BavarianData has enabled action, hour and minute sensors of at least one timer;
**Next departure** names the timer entities in use. Otherwise, take the
departure from a calendar or a date/time entity instead.

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
percentage ceiling. A pause the plan itself had set ends with it, so the car
charges again; any other setting of the wallbox stays as it is.

## Change or cancel a trip

Disabling **Next departure** clears its selected trip and cancels commands queued
by that plan. It keeps the ordinary percentage target and the current pause
state. Existing configured sources are recognized after an update; the vehicle
departure button keeps its existing entity identity and automations.

## What the plan changes while charging

The planner uses the current vehicle SoC, remaining time, usable capacity,
known wallbox limits and phase information. It works in three steps:

1. **Wait.** Charging stays paused until the energy still needed takes the
   remaining time at full current, plus a **30-minute reserve** for a charge
   slower than estimated. The `planned_start` attribute of **Timed charging
   status** shows that time.
2. **Full current.** Charging runs at the wallbox's maximum current.
3. **Finish at 6 A.** Once the rest fits the remaining time at 6 A, the plan
   reduces to 6 A and lands on the target at departure instead of early.

Because the current changes at most every five minutes, the plan looks one such
window ahead near the end. Instead of jumping straight from full current to
6 A, it can set an intermediate current for that window, rounded up to whole
amperes and never above the current cap, so that only the energy above the 6 A
path is spent early. A last window shorter than five minutes uses the time that
is actually left. A charge that is already ahead never goes below 6 A and does
not pause again just to use up time.

### Minimum charge level

Waiting for a late start leaves the car at its arrival level for hours. Set
**Minimum charge level (%)** in the departure planning options to keep a
reserve for an unplanned trip: while the car is below that level, the plan
charges at once at full current and shows **Charging to the minimum level**.
At the level it pauses and waits for the planned start as usual; the
`planned_start` attribute keeps showing that start. The level is part of the
target, not extra, and **0** switches it off. It applies per
[vehicle profile](vehicle-setup.md), with and without the Modbus service app.

The current still changes at most every five minutes, so charging can run a
little past the level. A car whose charge level drops below it again while it
waits charges back up to it.

Each SoC report recalculates this. A charge that is faster than estimated never
pauses again once it has started; it reaches the 6 A step sooner. A slower one
returns to full current. **An active timed goal can lift even a
manually set charging pause** to meet the selected time. To prevent that,
clear **Next departure**. The ordinary percentage target without a departure
time still only limits charging and never resumes a pause.

With [Virtual Plug & Charge](vehicle-setup.md#optional-virtual-plug--charge)
plugging in a recognized car does not start charging ahead of
an armed plan. The release waits until the plan has paused until its start or
set its current.

The plan applies only after the selected car confirms the
plug-in. Another car shows **Car not confirmed** and gets no plan; a pause the
plan set earlier ends. See
[the car's rules apply only to that car](vehicle-setup.md#the-cars-rules-apply-only-to-that-car).

## Climate before departure

The optional [calendar and preconditioning blueprints](blueprints.md) select
one trip and run your chosen vehicle action before departure. The vehicle
determines whether preconditioning draws grid or battery power; completed
charging targets still apply. Once the target is reached, the charging plan
cannot resume another charge. Raising **Vehicle charge
target** above the car's charge level plans the same trip again. Every target
change recalculates the plan. **Next departure** remains visible until that
time so the climate blueprint can still run, then clears automatically.
## Limits and missing data

The planner assumes 230 V per phase and initially 90% transfer efficiency.
The wallbox reports the car's phases only while it draws current. The plan
therefore remembers each vehicle profile's phases from its
last charge. A car never seen charging is planned with three phases. A one-phase
car then starts later than it should; its first charge corrects that from the
reserve, and every later trip plans with one phase.
Car limits, missing or delayed SoC reports, unknown vehicle power, the 6 A
minimum, the wallbox's configured transition delay and HA outages can make
the exact target time impossible. Missing SoC or capacity data leaves the
current charging state unchanged.

With a direct serial connection the plan needs HA running to recalculate the
current. Climate actions always need HA.

## With the Modbus service app

With the [Modbus service app](modbus-service.md) the app plans the trip
itself. Home Assistant hands over the departure time, the selected car with
its usable capacity and remembered phases, and every charge level together with
the time the car reported it. The percentage target reaches the app as before.
The entities in Home Assistant show the app's plan; you set, change and clear
the trip there as described above.

- **The plan keeps running without Home Assistant.** A restart or outage of HA
  changes nothing. After an app restart the plan continues when the wallbox
  proves that the car stayed plugged in; otherwise it waits until Home
  Assistant confirms the car again.
- **The plan starts only for a confirmed car with a known charge level.** Home
  Assistant confirms the selected car for each plug-in. Without a charge level
  the status is **Waiting for vehicle charge level**.
- **Between two reports the app projects the charge level**: the last reported
  level plus the metered energy since then at the car's transfer efficiency,
  90% until the app has learned it. Energy charged before a report is not
  counted twice, because every report carries its own time.
- **The app learns each car.** From two reports of the car during one plug-in
  that lie at least ten percentage points apart, and the energy metered
  between them, it learns how much of the metered energy reaches the battery
  (60% to 100%; a slightly wrong capacity is absorbed as well). Every
  comparison moves the value by a part only, so one late report cannot shift
  the start by much. While the plan charges at full current, it also meters
  the power the car really draws, per number of phases and per ampere offered,
  so it stays valid when the maximum current changes. A car that draws less than the wallbox offers therefore
  gets an earlier start, and the 30-minute reserve stays for the unexpected.
  Both values are kept per vehicle profile for up to eight cars and survive a
  restart.
- **The forecast runs to the percentage target.** **Timed charging status**
  shows it as attributes: `estimated_finish`, `estimated_level_at_departure`,
  `learned_efficiency` and, once metered, `measured_power_kw`. As long as the
  target is reachable, the plan lands on it at departure. Once it is not, the
  finish time lies after the departure and the level below the target.
- **At the projected target the app pauses** and shows **Charged to the
  estimated target**. This stop is provisional: a later report more than one
  percentage point below the target charges again, a report at or above it
  ends the trip as **Target reached**. Without another report the trip counts
  as reached at departure. A target at the car's own charge limit is not paused
  on an estimate, because the car ends that charge itself.
- **Manual and PV commands act at once.** The plan sets its current again five
  minutes after its last change at the earliest, and only when it differs by
  at least 1 A.
- **An order from the app has priority.** While a charging job, weekly plan or
  guest order holds the wallbox, the status is **An order from the app has
  priority** and the plan writes nothing.

The plan's pause ends when the car is unplugged or another car is selected, so
the next car is not left paused. It also ends when the departure is cleared or
passes while the plan still paused. Only a stop at the reached target is kept
until the car is unplugged.

The bus owner also checks for a disconnected vehicle before
applying a planned command. Confirmed unplugging cancels that command, including
while HA is offline in service mode. Manual controls remain available.

Current adjustments require at least 1 A difference and are spaced by at least
five minutes; the status sensor has
stable states and adds no per-minute history entries.

[Vehicle setup](vehicle-setup.md) · [Calendar and climate blueprints](blueprints.md) · [Troubleshooting](troubleshooting.md#targets-and-departure-planning)
