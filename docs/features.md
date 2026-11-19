# Optional charging features

[User guide](README.md) · [Dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Basic charging needs only the current slider and pause switch. Add a feature
when you need it; you do not need to configure every option.

## Choose what you want to achieve

| I want to… | I need… | Set it up |
|---|---|---|
| Add about 12 kWh, then stop | The wallbox's lifetime energy counter | [Energy target](user-guide.md#energy-target) |
| Charge for about 5 €, then stop | Configured charging costs | [Cost target](user-guide.md#cost-target) |
| Stop at 80% battery charge | A vehicle SoC source | [Percentage target](vehicle-setup.md#optional-vehicle-percentage-target) |
| Respect the car's own maximum percentage | Its reported charge-limit source, separately enabled | [Vehicle setup](vehicle-setup.md) |
| Reach a target for my next trip | SoC, usable battery capacity, positive percentage target and a dated departure | [Departure planning](departure-planning.md) |
| Copy my next trip from a calendar | A HA calendar and exact event title | [Calendar departure](departure-planning.md#calendar) |
| Use the next BMW climate timer | BavarianData and its enabled timer entities; not every car sends them | [Vehicle departure](departure-planning.md#bavariandata-climate-timer) |
| Start climate before departure | An existing HA vehicle action or script | [Preconditioning blueprint](blueprints.md#start-vehicle-climate-before-departure) |
| Choose between several cars | Optional vehicle profiles | [Vehicle selection](vehicle-setup.md#example-two-cars-manual-selection) |
| Recognize the connected car automatically | Optional profiles and cable/home signals | [Automatic selection](vehicle-setup.md#optional-automatic-vehicle-selection) |
| Release charging for a recognized car | An active profile with fresh cable/home detection | [Virtual Plug & Charge](vehicle-setup.md#optional-virtual-plug--charge) |
| Charge from solar surplus | A suitable power sensor and the PV blueprint | [PV charging](blueprints.md#set-up-pv-charging) |
| Start a guest charge now, optionally limited by kWh or a stop time | Write access | [Guest charging](user-guide.md#guest-charging) |
| Estimate charging costs | A fixed gross price or existing dynamic price sensor | [Charging costs](charging-estimates.md#charging-costs-fixed-or-dynamic-price) |
| Estimate losses | Battery energy from the matching vehicle charge | [Charging losses](charging-estimates.md#charging-losses-choose-the-available-data) |
| Get notified when the car is unplugged, the wallbox faults or a command fails | Nothing extra; the **Activity** entity | [React to activity](automations.md#react-to-activity) |
| Reduce database growth | Recorder exclusions and retention choices | [Recorder guide](recorder.md) |

## Which function may start or stop charging?

| Function | Starts or resumes charging? | Stops or limits charging? |
|---|---|---|
| Guest start | Starts now and grants this plug-in permission; a wait may still apply | First reached shared kWh/time limit stops it; vehicle percentage is ignored |
| Current slider | Can resume a paused charge | Limits current; **0 A removes the limit** |
| Pause switch | Off requests resume | On requests pause; a configured wait can still apply |
| kWh target | Setting it does not start; a pause caused by a temporary meter outage has [specific recovery rules](user-guide.md#energy-target) | Pauses at the energy target or when the meter cannot be trusted |
| Percentage target | Never starts by itself | Pauses at the reported percentage; at the car's own limit the car stops by itself; missing SoC does not pause |
| Timed departure plan | May resume, including a manual pause | May pause until its late start, then charges at full current and finishes at 6 A; completed targets still apply |
| Vehicle selection/detection | Never starts or resumes | Changes the data used by optional features and clears the previous car's percentage target/trip |
| Virtual Plug & Charge | Can release a freshly recognized plug-in and lift a previous manual pause; a restart grant preserves the pause. Locks and reached targets still apply | Keeps charging disabled until permission is granted; disabling the feature does not resume |
| Cost/loss estimates | No | No; these are estimates, not charging controls |

Wallbox authorization, hardware limits, locking, charging release and read-only
permission still apply. A grid-operator downgrade cannot be overridden by an
HA automation. [Safety and limitations](safety.md) explains the operating limits.

Start with one feature and check its status before enabling another. Avoid
running independent current controllers against the same wallbox. For a ready
control panel, use the [dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards).
