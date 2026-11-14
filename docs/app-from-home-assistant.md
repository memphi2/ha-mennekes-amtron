# Your wallbox on a Home Assistant dashboard

[Guide home](README.md) · [Everyday charging](user-guide.md) · [App interface](app-guide.md) · [Help](troubleshooting.md)

Choose a ready-to-paste card below. It uses the integration's own entities: no
extra helpers or separate wallbox card to maintain. HA and the app operate on
the same shared limits; optional vehicle planning is configured in HA.

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

## What the card shows

One status tile and two rows of chips, so it fits next to other tiles:

1. **Tile:** `AMTRON · power`, then charging state and session energy, then
   the active limits, for example `Limits: 10 kWh · until 07:30 · 80 %`, and
   the planning state when vehicle planning is configured. Green means
   charging, amber paused, red a fault.
2. **Control chips:** **P&C** (green once the car is authorized, amber while
   it waits), the selected vehicle, the current limit (**0 A** removes the
   limit; it does not pause), **Pause** (tap toggles) and **Release**.
3. **Limit chips:** kWh target, **target time**, and with a vehicle profile its
   percentage target. Tap a chip to edit it: the time chip opens date and time
   input; **hold** it to clear the time. **Start guest** appears only while no
   guest is charging; **Pause** stops a guest. **Calendar** and **Vehicle
   timer** appear only when vehicle planning offers them.

The card uses only the integration's entities and
`mennekes_amtron_rtu.clear_target_time`; no helpers are needed.

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
After an app restart, the permission is restored only for the proven same plug-in.
Otherwise the app stops charging and needs a new explicit guest start.
If a start response is lost, refresh and inspect the current state before retrying.

## Automation actions

Set shared limits first, then use `mennekes_amtron_rtu.start_guest_charging`.
Supply `device_id` if you have more than one wallbox. Stop with the ordinary pause switch.

```yaml
- action: mennekes_amtron_rtu.start_guest_charging
```

Old guest drafts, duration/current fields, stop/clear actions and the job API have
been removed. Replace old dashboard YAML instead of adding missing helper entities.
