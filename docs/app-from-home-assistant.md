# Your wallbox on a Home Assistant dashboard

[Guide home](README.md) · [Everyday charging](user-guide.md) · [App interface](app-guide.md) · [Help](troubleshooting.md)

Choose a ready-to-paste card below. It uses the integration's own entities: no
extra helpers or separate wallbox card to maintain. It works for a direct USB
or LAN entry and for an app entry alike; with the app, HA and the app operate
on the same shared limits. Optional vehicle planning is configured in HA.

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

1. Install Mushroom. Controls, guest charging and targets need write access:
   **Modbus master** for a direct entry, or the app's **Read-only** off with the
   [service app](modbus-service.md).
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
   the planning state when vehicle planning is configured. Green means charging;
   grey means initialization or status checking. Amber means paused, missing
   readings or an ambiguous state; red means a wallbox fault or a lost connection.
   Read the status text for the specific reason.
2. **Control chips:** **P&C** (green once the car is authorized, amber while
   it waits), the selected vehicle, the current limit (**0 A** removes the
   limit; it does not pause), **Pause** (tap toggles) and **Release**.
3. **Target chips:** **Energy target**, **Target time**, and with a vehicle
   profile its **Vehicle charge target**. The card has no chip for the cost
   target; editing the energy target ends a running cost target. Tap a chip to edit it: the time chip opens date and time
   input; **hold** it to clear the time. **Start guest** appears only while no
   guest is charging; **Pause** stops a guest. **Calendar** and **Vehicle
   timer** appear with vehicle profiles; they run the `use_calendar_departure`
   and `use_vehicle_departure` actions, which report an error when that source
   is not configured.

The card uses only the integration's entities and actions; no helpers are
needed.

## Energy and vehicle charge target

[Shared targets](user-guide.md#shared-targets-and-target-time) explain which targets
apply. A positive energy target counts from the current meter reading. Saving a
target does not start charging. **0** disables the energy or vehicle charge target.

## Plan departure

Configure the recognized vehicle's SoC and usable capacity in HA, then set its
vehicle charge target and target time. [Departure planning](departure-planning.md)
explains waiting, actual-power learning, calendar imports and preconditioning.
With the app, its pages show the same shared time; there is no separate
planning editor.

## Guest charging

Connect the guest, optionally set the energy target and target time, then press
the guest button. It starts now; the energy target, cost target or target time
ends it, whichever comes first. While active,
the card's button requests the ordinary pause. [Full behavior and restart limits](user-guide.md#guest-charging).

## Manual selection, automatic recognition and Plug & Charge

Normal controls need no vehicle integration. Automatic recognition and virtual
Plug & Charge are separate optional features. Guest start grants permission only
for the connected guest; it does not disable Plug & Charge or change your vehicle
profile. [Set up vehicles](vehicle-setup.md).

## Storage and failed handovers

The wallbox service keeps the saved limits; with the app, HA and the app read
the same ones. Guest permission itself is temporary; there is no saved guest
order. With the app, an HA Core restart leaves the service running; a direct
entry's service restarts with HA. After a service restart, the permission is
restored only for the proven same plug-in. Otherwise the service stops charging
and needs a new explicit guest start.
If a start response is lost, refresh and inspect the current state before retrying.

## Automation actions

Set shared limits first, then use `mennekes_amtron_rtu.start_guest_charging`.
Supply `device_id` if you have more than one wallbox. Stop with the ordinary pause switch.

```yaml
- action: mennekes_amtron_rtu.start_guest_charging
```

**Start guest charging** and **Guest charging** are the matching button and
binary sensor; see [Entity reference](entities.md#guest-charging-and-shared-time).
