# Choose your optional functions

[Documentation](README.md) › Optional functions

A working wallbox needs no vehicle integration, calendar or price sensor.
Start with ordinary current control and pause/resume, then add one function at
a time. Each configuration section saves independently and shows the inputs
needed for your choice.

This guide follows **beta 0.8.0b5**, which includes central vehicle profiles, optional
automatic selection, Virtual Plug & Charge and live app Modbus debugging.
**0.6.1 remains stable.** See
[versions and availability](README.md#versions-and-feature-availability).

## Choose what you want to achieve

| I want to… | I need… | Set it up |
|---|---|---|
| Add about 12 kWh, then stop | The wallbox's lifetime energy counter | [Energy target](user-guide.md#energy-target) |
| Stop at 80% battery charge | A vehicle SoC source | [Percentage target](vehicle-setup.md#optional-vehicle-percentage-target) |
| Respect the car's own maximum percentage | Its reported charge-limit source, separately enabled | [Vehicle setup](vehicle-setup.md) |
| Reach a target for my next trip | SoC, usable battery capacity, positive percentage target and a dated departure | [Departure planning](departure-planning.md) |
| Copy my next trip from a calendar | A HA calendar and exact event title | [Calendar departure](departure-planning.md#calendar) |
| Use the next BMW climate timer | BavarianData and its enabled timer entities | [Vehicle departure](departure-planning.md#bavariandata-climate-timer) |
| Start climate before departure | An existing HA vehicle action or script | [Preconditioning blueprint](blueprints.md#start-vehicle-climate-before-departure) |
| Choose between several cars | Optional vehicle profiles | [Vehicle selection](vehicle-setup.md#example-two-cars-manual-selection) — beta 0.8.0b4 |
| Recognize the connected car automatically | Optional profiles and cable/home signals | [Automatic selection](vehicle-setup.md#optional-automatic-vehicle-selection) — beta 0.8.0b4 |
| Release charging for a recognized, permitted car | A permitted active profile and fresh cable/home detection | [Virtual Plug & Charge](vehicle-setup.md#optional-virtual-plug--charge) — beta 0.8.0b4 |
| Charge from solar surplus | A suitable power sensor and the PV blueprint | [PV charging](blueprints.md#set-up-pv-charging) |
| Estimate charging costs | A fixed gross price or existing dynamic price sensor | [Charging costs](charging-estimates.md#charging-costs-fixed-or-dynamic-price) |
| Estimate losses | Battery energy from the matching vehicle charge | [Charging losses](charging-estimates.md#charging-losses-choose-the-available-data) |
| Reduce database growth | Recorder exclusions and retention choices | [Recorder guide](recorder.md) |

## Example: one car, no vehicle integration

Use **Charging current limit** to choose the current and **Charging paused** for
everyday pauses. If you want to supply a limited amount of energy, set **Energy
target** to 12 kWh and start charging normally. The target only limits charging;
setting it does not lift a pause. Actual delivered energy can overshoot slightly
because readings and commands take time.

## Example: one car with a SoC sensor

Under **Configure → Vehicle**, select that car's charge-level entity and enable
only the percentage target. Set **Vehicle charge target** to 80%. Add the car's
maximum-limit sensor only if you want its value to bound the selectable target.
No departure time or capacity estimate is needed for a simple percentage limit.
If SoC reports disappear, charging continues; the car's own charge limit remains
useful independently of HA.

## Example: several cars at the same wallbox

Create a profile for each car, select one and configure its optional features.
Repeat for the other car. Select a manual profile **before charging**; the
active selection determines which SoC, departure and loss sources are used.
Manual selection is the simplest default. Enable automatic selection only when
your available cable and home signals are reliable enough for that purpose.
An uncertain automatic match selects no car instead of borrowing another car's
target. Profile changes never start charging.

To also control charging permission, enable **Virtual Plug & Charge** separately
and permit each intended profile. It waits for fresh detection after plugging in;
selecting a profile by hand is not enough. Automatic selection can remain off.
Existing pauses, locks and reached targets remain effective.

## Example: ready for tomorrow, with climate

Set a positive percentage target and choose tomorrow's departure date/time.
Supply usable battery capacity, then check the selected trip. You can instead
copy the time from a calendar event or the next available BMW climate timer.
Use the climate blueprint for your chosen action and lead time.
**A timed plan may resume a manual pause.** Clear the departure time to cancel
that plan; the ordinary percentage limit remains independent.

## Which function may start or stop charging?

| Function | Starts or resumes charging? | Stops or limits charging? |
|---|---|---|
| Current slider | Can resume a paused charge | Limits current; **0 A removes the limit** |
| Pause switch | Off requests resume | On requests pause; a configured wait can still apply |
| kWh target | Setting it does not start; a pause caused by a temporary meter outage has [specific recovery rules](user-guide.md#energy-target) | Pauses at the energy target or when the meter cannot be trusted |
| Percentage target | Never starts by itself | Pauses at the reported percentage; missing SoC does not pause |
| Timed departure plan | May resume, including a manual pause | May pause to spread charging toward the trip; completed targets still apply |
| Vehicle selection/detection | Never starts or resumes | Changes the data used by optional features and clears the previous car's percentage target/trip |
| Virtual Plug & Charge | Can release charging for a freshly recognized, permitted car; does not lift an existing pause, lock or reached target | Keeps charging disabled until permission is granted; disabling the feature does not resume |
| Cost/loss estimates | No | No; these are estimates, not charging controls |

Wallbox authorization, hardware limits, locking, charging release and read-only
permission still apply. A grid-operator downgrade cannot be overridden by an
HA automation. [Safety and limitations](safety.md) explains the operating limits.

## Add features without losing track

Use one change at a time, then check its new entity or status before adding the
next feature. Cancel an unfinished configuration section to keep its existing
settings. A later automation can change a manual choice, so avoid running two
independent current controllers against the same wallbox.

[Everyday use](user-guide.md) · [Vehicle setup](vehicle-setup.md) · [Departure planning](departure-planning.md) · [Troubleshooting](troubleshooting.md)
