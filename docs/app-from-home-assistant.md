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
   write access and the minimum app version required by the integration. Targets and departure
   planning also work with a direct connection; HA must then remain running.
2. Copy the YAML for your language using GitHub's **Raw** view.
3. Check IDs under **Settings → Devices & services → Entities**. The examples use
   `mennekes_amtron` and translated names. HA chooses IDs when entities are created;
   changing the UI language later does not rename them. Adjust IDs for your device,
   earlier names or multiple wallboxes.
4. **Edit dashboard → Add card → Manual**, paste the complete YAML and save.
   Its root is `vertical-stack`; `masonry` belongs to a dashboard view instead.
5. Leave edit mode to check conditional visibility. HA shows conditional cards
   while editing. Optional target and planning controls appear only when their entities are available.

The top tile shows live power, session energy and charging state. State icons and
colors change: charging/ready is green, waiting/paused amber, a fault red, and
an inactive function grey. Text always explains the state as well. **P&C ready**
means authorization was reported; merely turning P&C on does not mean the car
has been recognized. Tap **Pause** to toggle it. Release and P&C open their
native HA controls; the vehicle chip opens profile selection. **0 A removes the
current limit; it does not pause charging.**

## What the card shows

The card follows daily use from top to bottom:

1. **Wallbox now:** state, power and session energy, with colored state icons.
2. **Controls:** pause, release, Plug & Charge, selected vehicle and current slider.
3. **Charging limits:** shared kWh, target time and optional vehicle percentage.
4. **Guest charging:** one button, changing to pause while the guest is active.
5. **Vehicle planning:** only when configured; status and optional calendar/vehicle imports.

Tap the time tile to edit it; hold it to clear the selected time. This uses the
integration's existing datetime entity and `mennekes_amtron_rtu.clear_target_time`.
There is no helper or separate clear-button entity to maintain.

## Charge target

[Shared limits](user-guide.md#shared-limits-and-target-time) explain which targets
apply. A positive kWh setting establishes a new ceiling from the current meter.
Saving a limit does not start charging. **0** disables an energy or percentage limit.

## Plan departure

Configure the recognized vehicle's SoC and usable capacity in HA, then set its
percentage target and next trip time. [Departure planning](departure-planning.md)
explains waiting, actual-power learning, calendar imports and preconditioning.
The app shows the same shared time; it needs no separate planning editor.

## Guest charging

Connect the guest, optionally set kWh and time, then press the guest button.
It starts now and stops at whichever configured limit arrives first. While active,
the card's button requests the ordinary pause. [Full behavior and restart limits](user-guide.md#guest-charging).

## Manual selection, automatic recognition and Plug & Charge

Normal controls need no vehicle integration. Automatic recognition and virtual
Plug & Charge are separate optional features. Guest start grants permission only
for the connected guest; it does not disable Plug & Charge or change your vehicle
profile. [Set up vehicles](vehicle-setup.md).

## Storage and failed handovers

HA and the app read the same saved limits. Guest permission itself is temporary;
there is no saved guest order. An HA Core restart leaves the service running.
After an app restart, check state and explicitly start the guest again.
If a start response is lost, refresh and inspect the current state before retrying.

## Automation actions

Set shared limits first, then use `mennekes_amtron_rtu.start_guest_charging`.
Supply `device_id` if you have more than one wallbox. Stop with the ordinary pause switch.

```yaml
- action: mennekes_amtron_rtu.start_guest_charging
```

Old guest drafts, duration/current fields, stop/clear actions and the job API have
been removed. Replace old dashboard YAML instead of adding missing helper entities.
