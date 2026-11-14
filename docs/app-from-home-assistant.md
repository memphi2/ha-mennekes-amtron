# Dashboard cards and app actions

[User guide](README.md) · [Dashboard cards](app-from-home-assistant.md#dashboard-with-mushroom-cards) · [Help](troubleshooting.md)

Choose a linked YAML template to put charging controls on your Home Assistant
home screen. Start with the basic card; the charging-job, guest and boost controls
require the optional [Modbus service app](modbus-service.md).

The [app's own web interface](app-guide.md) remains available alongside the
dashboard. Action fields and automation examples are at the bottom of this page.

## Dashboard with Mushroom cards

The **basic** example uses built-in HA cards and works with direct USB, LAN or
the app. The **app dashboard** uses [Mushroom](https://github.com/piitaya/lovelace-mushroom)
and native integration entities. Install Mushroom through HACS first.
Both examples are single cards: paste them under **Edit dashboard → Add card →
Manual**. No helpers, scripts, packages or custom wallbox card are needed.

| Language | Basic controls | Mushroom app dashboard |
|---|---|---|
| Deutsch | [YAML](../examples/dashboard/basic.de.yaml) | [YAML](../examples/dashboard/app.de.yaml) |
| English | [YAML](../examples/dashboard/basic.en.yaml) | [YAML](../examples/dashboard/app.en.yaml) |
| Français | [YAML](../examples/dashboard/basic.fr.yaml) | [YAML](../examples/dashboard/app.fr.yaml) |
| Nederlands | [YAML](../examples/dashboard/basic.nl.yaml) | [YAML](../examples/dashboard/app.nl.yaml) |
| Italiano | [YAML](../examples/dashboard/basic.it.yaml) | [YAML](../examples/dashboard/app.it.yaml) |

### Add the dashboard

1. For the Mushroom app card, connect the integration to the service app and
   enable write access. Integration and app must be **0.9.0b6 or newer**.
   The basic card also works with a direct USB or LAN connection.
2. Copy the YAML for your language (GitHub's **Raw** button shows plain text).
3. Check entity IDs under **Settings → Devices & services → Entities**. The
   examples use `mennekes_amtron` and the translated entity names. HA uses the
   language at entity creation; later language changes do not rename IDs.
   Replace IDs if you renamed the device, use another language or have several
   wallboxes. Every button targets its wallbox's entity explicitly.
4. Open **Edit dashboard → Add card → Manual**, replace the editor contents
   with the complete example and save. The root type is `vertical-stack`.
   `masonry` is a dashboard-view layout and does not belong in this card editor.

### Using the compact Mushroom card

The top tile shows charging status, live power and energy for this session.
Unavailable readings show a dash, never a made-up zero. Tap the tile for details.
The current slider changes the limit; **0 A removes the limit, it does not pause**.

Tap **Pause** to pause or resume. **Release**, **Plug & Charge** and **Vehicle**
open their entity controls so you can check the setting before changing it.
These chips appear only when their entities are available. An active switch
has a highlighted icon. A waiting reason appears when charging is held up.

The lower section holds the shared charging-job draft: current, energy and
**Finish by**, followed by short Start, Stop, Clear and Reset buttons. Each
button asks for confirmation. If the draft entity is missing or unavailable,
one information tile replaces the job controls. Check both versions, the app
connection and the entity IDs. A finish time that has not been set yet is normal;
it does not hide the form.

Leave dashboard edit mode to check visibility: Home Assistant also displays
conditional cards while editing. The YAML does not change app settings or
create extra helpers, and it does not include a separate vehicle card.

### One draft in the app and Home Assistant

Set **Job current**, **Job energy** and **Job latest finish** in the
card. Each edit saves to the app and appears in its charging-jobs page after
refresh. In the app, use **Save draft** to publish your edits to HA. A draft
survives restarting HA or the app, but never starts charging by itself.

**Start charging job** checks the saved values and creates the order for the
currently connected car. The YAML asks for confirmation first. The **Charging
job draft** status explains whether a finish time, connected car or permission
is missing. Editing the draft does not change a running order.

**Stop charging job** pauses the order. **Clear charging job** removes
its record without resuming. **Reset job draft** restores empty times,
6 A and 10 kWh; it leaves the current order unchanged and can also recover an
unreadable draft file.

For advanced scheduling, enable **Job earliest start** and **Job immediate
reserve** in the entity settings and add them to your view. They are disabled
by default. An empty start means now when confirmed; reset the draft to clear
a previously set start. The reserve is part of the energy ceiling, not extra.

If someone edits the draft in another window, an older edit or preview is
rejected. Reload the saved draft, check it and retry. This prevents silently
overwriting another person's settings. The app can also edit a draft while
an order runs, or before a car is connected.

Boost and guest charging remain available in the app and through the actions
below. Departure planning has its own entities; it is separate from this draft.

## What you get

| In Home Assistant | What it does |
|---|---|
| `sensor.*_charging_job` | State of the app's charging job or guest charge, or **No job**. Attributes: `kind`, `start`, `finish`, `current`, `energy`, `delivered`, `reserve`, `reason`, `boost_until`, `boost_available`. |
| Action `start_charging_job` | Charge in a time window with a fixed current up to an amount of energy. |
| Action `start_guest_charging` | Charge a guest's car from now on, limited in time and energy. |
| Action `stop_charging_job` | Stop the running job or guest charge and pause charging. The job does not resume; a [departure plan](departure-planning.md) that is due may charge again. |
| Action `clear_charging_job` | Remove an ended or stopped job so that a new one can start. |
| Action `start_boost` / `cancel_boost` | Charge right away although the job starts later, then return to the plan. |

All actions are named `mennekes_amtron_rtu.<action>`. The sensor exists only
with the app; with a direct USB or LAN connection the actions answer that they
need the app.

## Action fields

Every action takes an optional `device_id`. With one wallbox it can be left
out; with several, name the wallbox.

| Action | Field | Meaning |
|---|---|---|
| `start_charging_job` | `finish` | When charging ends at the latest, within seven days. Required. |
| | `start` | When charging may begin. Left out: now. |
| | `current` | 6–32 A per phase, at most the app's current cap. Required. |
| | `energy` | Charging stops after this many kWh. Required. |
| | `reserve` | Optional: this many kWh are charged right away, before the window. |
| `start_guest_charging` | `duration` | Minutes from now, up to 1440. Required. |
| | `current`, `energy` | As above. Required. |
| `start_boost` | `duration` | Minutes to charge early, up to 120. Required. |

A time without a time zone is taken as local time. A window that ends before it
starts, starts in the past or ends more than seven days ahead is refused.
Energy counts in steps of 0.1 kWh; a finer value is rounded. The reserve must
not exceed the energy.

A stop always acts at once. A start waits for the app's minimum time between
state changes, so an automation that starts and stops in quick succession
switches the car on and off at most once per interval.

## One tap, and what happens behind it

On its own pages the app first shows a preview of an order and asks you to
confirm it: the preview holds the checked values and is tied to the current
plug-in, so that an order cannot reach another car or an outdated state.

An action from Home Assistant asks for the same preview and confirms it at
once. Nothing is skipped: if the car was unplugged, another order is running
or a value is not allowed, the action fails with the app's reason and nothing
is started. For the question "are you sure?", give the dashboard button a
confirmation, as in the linked dashboard examples. Automations call the same actions
without a question.

## In automations

```yaml
- action: mennekes_amtron_rtu.start_guest_charging
  data:
    duration: 180
    current: 10
    energy: 15
```

A failed action raises an error with the app's reason, for example that
another order is still running; an automation stops there unless it handles
the error with `continue_on_error`.
