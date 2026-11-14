# Charging made simple

[User guide](README.md) · [App](app-guide.md) · [Help](troubleshooting.md)

Use the usual current slider and pause control. Add a kWh limit, a time or a
vehicle percentage target when you need one. Saving a limit does not start charging.
Guest charging has one explicit start button. Intelligent planning for a recognized
vehicle remains in Home Assistant; the app has no separate planning page.

## Dashboard with Mushroom cards

The basic example uses built-in HA cards. The full example needs
[Mushroom](https://github.com/piitaya/lovelace-mushroom), available through HACS.
Both are a **single Manual card**, not an entire dashboard view. No extra helpers,
scripts, packages, custom wallbox card or card-mod are required.

| Language | Basic controls | Mushroom card |
|---|---|---|
| Deutsch | [YAML](../examples/dashboard/basic.de.yaml) | [YAML](../examples/dashboard/app.de.yaml) |
| English | [YAML](../examples/dashboard/basic.en.yaml) | [YAML](../examples/dashboard/app.en.yaml) |
| Français | [YAML](../examples/dashboard/basic.fr.yaml) | [YAML](../examples/dashboard/app.fr.yaml) |
| Nederlands | [YAML](../examples/dashboard/basic.nl.yaml) | [YAML](../examples/dashboard/app.nl.yaml) |
| Italiano | [YAML](../examples/dashboard/basic.it.yaml) | [YAML](../examples/dashboard/app.it.yaml) |

1. Install Mushroom. Guest charging requires the [service app](modbus-service.md),
   write access and matching integration/app versions. Targets and departure
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

- **Additional energy:** wallbox kWh, independent of a vehicle integration.
  Saving a positive value arms a new ceiling from the current meter reading.
  Starting guest charging keeps that saved baseline; it does not add the amount again.
- **Vehicle charge level:** optional percentage target for the selected vehicle.
  Configure its SoC source and optional maximum under **Configure → Vehicles**.
- **0** disables an energy or percentage limit without lifting a pause.

HA and **App → Charging** use the same saved choices. In the app, save each edited
field before starting a guest. Missing vehicle data is not guessed.

## Plan departure

For a recognized vehicle, enable next-departure planning in HA and provide SoC
and usable battery capacity. Set the percentage target and choose the next trip's
date/time. The existing intelligent planner chooses the start and adjusts current.
Calendar and supported vehicle departure imports remain optional HA buttons.
The single shared time entity serves both guest charging and vehicle planning.
In the Mushroom example, tap it to edit and hold it to clear the time.
Automations use `mennekes_amtron_rtu.clear_target_time` with that datetime entity;
there is no separate clear-button entity.
There is no extra app planning workflow; its time field shows the shared selection.

A plan may lift a manual pause. Clear the selected time to cancel planning; then
pause explicitly if the car must stay stopped. Clearing a time does not clear the
energy or vehicle target. See [departure planning](departure-planning.md).

## Guest charging

1. Connect the visitor's car.
2. Optionally save a kWh limit and/or future time, in HA or **App → Charging**.
3. Press **Start guest charging** once. It starts immediately, lifts a manual pause
   and grants virtual Plug & Charge permission for this connection. Wallbox locks,
   faults, local authorization requirements and electrical limits still apply.

The first configured kWh or time limit reached stops charging. With neither set,
there is no implicit energy or time limit. An already reached energy target is not
reset by the button. A past time must be cleared or changed before starting.

The vehicle percentage target stays saved but does not limit a guest charge.
Changes to that vehicle target do not turn the guest into the selected vehicle.
Use the ordinary **Pause** control to stop. Unplugging ends guest permission; a
new connection needs a new explicit start. Guest charging requires the service app.

## Manual selection, automatic recognition and Plug & Charge

Manual controls work without a vehicle integration. A percentage target and
intelligent planning need a selected, confirmed vehicle. Optional automatic
recognition chooses only a unique match; delayed or ambiguous signals wait.
Virtual Plug & Charge can release recognized approved vehicles automatically.
The guest button gives a separate explicit permission for the current connection
without disabling Plug & Charge or changing the saved vehicle profile.

## Storage and failed handovers

Limits, selected time and active guest session persist in the service. Polling does
not repeatedly write them or add a charging journal. The app retains accepted intent
across an HA restart. After an app restart a guest can continue only when the wallbox
proves the same physical connection. A damaged record is not fresh permission.

A failed or uncertain start response is never automatically retried. Reload the
current state before deciding what to do next.

## Automation actions

Use the parameter-free action `mennekes_amtron_rtu.start_guest_charging`.
Supply `device_id` when there is more than one wallbox. Set shared limits first.
Stop through the ordinary pause control.

```yaml
- action: mennekes_amtron_rtu.start_guest_charging
```

The separate guest draft, duration/current fields, stop/clear actions and old job
API are removed before 1.0. Update old cards and automations.
