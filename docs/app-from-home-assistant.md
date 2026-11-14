# Three ways to charge

[User guide](README.md) · [App](app-guide.md) · [Help](troubleshooting.md)

Choose what you want to achieve. There is no separate charging-job mode.
Ordinary manual charging still uses the current slider, pause and release controls.

| Mode | Example | What starts charging? | What stops it? |
|---|---|---|---|
| **Charge target** | Add 20 kWh, or charge the selected car to 80% | You, your existing automation or authorized Plug & Charge. Setting a target never starts or resumes charging. | The first active energy/percentage limit reached. |
| **Plan departure** | Have the selected car ready tomorrow at 07:30 | The plan chooses when to start and adjusts current. It may lift a manual pause. | The percentage target, another active limit or the end of the trip. |
| **Guest charging** | Allow a visitor up to 15 kWh for at most 120 minutes at 10 A | Explicitly confirm **Start guest charging**, after authorization. | Energy limit, maximum duration, unplugging or **Stop**. |

The kWh values are measured at the wallbox, not energy added to the battery.
Targets and estimates do not guarantee a particular delivered amount or an exact
completion time. Vehicle and wallbox limits, delayed readings and hardware wait
intervals still apply.

## Dashboard with Mushroom cards

The basic example uses built-in HA cards. The full example needs
[Mushroom](https://github.com/piitaya/lovelace-mushroom), available through HACS.
Both are a **single Manual card**, not an entire dashboard view. No extra helpers,
scripts, packages, custom wallbox card or card-mod are required.

| Language | Basic controls | Three-mode Mushroom card |
|---|---|---|
| Deutsch | [YAML](../examples/dashboard/basic.de.yaml) | [YAML](../examples/dashboard/app.de.yaml) |
| English | [YAML](../examples/dashboard/basic.en.yaml) | [YAML](../examples/dashboard/app.en.yaml) |
| Français | [YAML](../examples/dashboard/basic.fr.yaml) | [YAML](../examples/dashboard/app.fr.yaml) |
| Nederlands | [YAML](../examples/dashboard/basic.nl.yaml) | [YAML](../examples/dashboard/app.nl.yaml) |
| Italiano | [YAML](../examples/dashboard/basic.it.yaml) | [YAML](../examples/dashboard/app.it.yaml) |

1. Install Mushroom. Guest charging requires the [service app](modbus-service.md),
   write access and **integration/app 0.9.0b7 or newer**. Targets and departure
   planning also work with a direct connection; HA must then remain running.
2. Copy the YAML for your language using GitHub's **Raw** view.
3. Check IDs under **Settings → Devices & services → Entities**. The examples use
   `mennekes_amtron` and translated names. HA chooses IDs when entities are created;
   changing the UI language later does not rename them. Adjust IDs for your device,
   earlier names or multiple wallboxes.
4. **Edit dashboard → Add card → Manual**, paste the complete YAML and save.
   Its root is `vertical-stack`; `masonry` belongs to a dashboard view instead.
5. Leave edit mode to check conditional visibility. HA shows conditional cards
   while editing. Missing optional features show setup guidance instead of controls.

The top tile shows live power, session energy and charging state. State icons and
colors change: charging/ready is green, waiting/paused amber, a fault red, and
an inactive function grey. Text always explains the state as well. **P&C ready**
means authorization was reported; merely turning P&C on does not mean the car
has been recognized. Tap **Pause** to toggle it. Release and P&C open their
native HA controls; the vehicle chip opens profile selection. **0 A removes the
current limit; it does not pause charging.**

## Charge target

- **Additional energy:** set the additional wallbox kWh to allow. No vehicle
  integration is needed. Every explicit save arms a new ceiling from the current meter reading,
  even when you save the same amount again.
- **Vehicle charge level:** optionally configure a SoC source under
  **Configure → Vehicles**, then choose a percentage. A configured vehicle maximum
  is respected. If that maximum is required but unknown, a new target is refused.
- **0** disables the corresponding limit, without lifting a pause. If both limits
  are active, both apply; the first reached ends charging.

In HA, editing the native number saves the choice. In the app open **Charging →
Charge target**, edit the value and explicitly save. App mode uses the same
persistent service state; there is no second independent target in the browser.
Missing vehicle data is not guessed. The configured behavior for unavailable SoC
continues charging until valid data returns, subject to the other limits.

## Plan departure

1. Configure the vehicle's SoC source, enable departure planning and provide usable
   battery capacity under **Configure → Vehicles → Next departure**.
2. Set the vehicle percentage target, for example 80%.
3. Choose a full date and time in HA or **App → Charging → Plan departure**.
4. Check the planning status. The plan waits, charges at higher current and tapers
   toward 6 A as appropriate. It can report that the target may not be reachable.

This is **one next trip**, not a daily recurring schedule. Calendar and supported
vehicle-timer imports remain optional HA buttons; they copy into this same
selected departure. Source configuration and capacity belong in HA. The app can
edit the resulting time once that vehicle's planning inputs are available.

**A plan may lift even a manual pause.** Clear the departure to cancel the plan.
Percentage and energy limits remain. A pause owned by the plan can end when the
plan is cleared; clearing is not a universal stop command. To keep the car stopped,
clear the trip and explicitly pause charging. See
[departure planning](departure-planning.md) for forecasts, calendar import,
climate actions and behavior during missing vehicle data.

## Guest charging

1. Plug in the visitor's car and authorize it locally, for example by RFID.
2. Explicitly disable an active **vehicle percentage target** first. Guest charging
   does not borrow your car's SoC, capacity or target and never silently removes it.
3. In HA or **App → Charging → Guest charging**, set **Energy**, **Maximum duration
   in minutes** and **Current per phase**. These are the same saved guest draft.
4. HA: press **Start guest charging** and confirm. App: save/preview, check the
   absolute end time, then confirm the preview within one minute.

Changing or resetting the draft never changes a running guest charge. A new
confirmation is bound to the same physical plug-in and the same saved draft.
A second browser or HA edit invalidates an older confirmation. Reload and review
changed values; a failed response is never retried automatically.

**Stop** ends the guest charge and requests a pause. **Clear** removes an ended
session without resuming. **Reset draft** restores 6 A, 10 kWh and 120 minutes;
it leaves a running guest charge unchanged. Reconnecting requires a new start.
The service retains the active guest session across an HA restart. A service
restart requires proof that the same car stayed connected before continuing.

## Manual selection, automatic recognition and Plug & Charge

| Setup | Targets and departure | Guest charging |
|---|---|---|
| No vehicle integration | kWh limit and manual controls work. A percentage target/departure plan needs vehicle data. | Works without a profile after explicit authorization. |
| Manually selected profile | Select the correct car. Its rules apply only once that car's plug-in is confirmed. | Do not apply the selected car's battery target to the visitor. |
| Automatic recognition | Optional cable/home signals choose a uniquely matching profile. Delayed or ambiguous signals wait; they do not guess. | Recognition does not automatically create a guest session. |
| Virtual Plug & Charge | Only recognized, approved cars receive automatic release. An armed departure plan still determines when to charge. | Does not bypass P&C, a lock or missing release. For an unrecognized visitor, explicitly enable **Charging release** in HA for this connection and authorize the car locally. P&C remains enabled; its manual grant ends when the car is unplugged. |

Manual starts, guest confirmation and planning never bypass the wallbox's lock,
release or electrical limits. An active guest session owns current control; stop
and clear it before taking over with manual current or another planner. Changing
vehicle percentage targets during guest charging can interrupt it. A departure
plan remains visible but waits while guest charging owns the wallbox.

## Storage and failed handovers

Targets, selected trip and guest draft/session are separate records. Saving a draft
is not a start command. Polling does not append a charging journal or write drafts
repeatedly. If a save is uncertain, the UI reports it; reload the authoritative
state before trying again. A damaged record is not silently treated as a new
charging permission. Recorder is not used to restore a draft.

## Automation actions

Use `mennekes_amtron_rtu.start_guest_charging`, `stop_guest_charging` and
`clear_guest_charging`. Supply `device_id` when there is more than one wallbox.
The start action checks a preview and confirms it once against the same plug-in.

```yaml
- action: mennekes_amtron_rtu.start_guest_charging
  data:
    duration: 120
    current: 10
    energy: 15
```

Actions raise an error if authorization, a valid meter or other prerequisites are
missing. They do not silently cancel conflicting settings or retry a start.
The former scheduled-job and boost actions are removed before 1.0; update old
cards and automations instead of retaining compatibility aliases.
