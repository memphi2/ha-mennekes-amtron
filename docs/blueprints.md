# Ready-made automations

The integration includes six blueprints. Adding the wallbox installs them
under **Settings → Automations & scenes → Blueprints**. Choose a blueprint,
select **Create automation**, then choose the entities for the same wallbox.
The PV sensor comes from your inverter or energy-management setup.

| Blueprint | Purpose |
|---|---|
| **Charge from PV surplus** | Adjust the current while a vehicle is connected and charging is not paused |
| **Pause and resume on PV surplus** | Pause below one power threshold and resume above a higher one, after a hold time |
| **Recover from a lost heartbeat** | Legacy manual-button automation; recovery is now built in. Disable existing instances to avoid duplicate attempts. |
| **Notify on a grid-operator downgrade** | Notify when a downgrade starts and ends |
| **Plan the next trip from a calendar** | Copy one matching departure into the timed charging plan |
| **Precondition before the next trip** | Run your vehicle climate action once before the selected departure |

## Plan the next trip from a calendar

Configure the vehicle SoC source and SoC target, then enable **Next departure** and
choose its battery-capacity source; see
[Charge for the next departure](user-guide.md#charge-for-the-next-departure). The
calendar supplies the departure time. It does not create a charge target.

1. Add your calendar to Home Assistant and create a **timed** event, for example
   **Departure**, at the time you want to leave. All-day events are ignored.
2. Under **Settings → Devices & services → Helpers**, create a **Toggle** helper,
   for example **Plan next trip**.
3. Create an automation from **Plan the next trip from a calendar**. Select the
   calendar, the exact event title, the helper and the wallbox's **Next
   departure** entity. Titles are case-sensitive.
4. Choose how far ahead to plan: the default is **12 hours**, adjustable up to
   **14 days**. Allow enough time for the energy your vehicle needs.
5. Turn on **Plan next trip**. The automation selects the earliest matching
   future event inside that window and turns the helper off after copying it.

If no matching event is available yet, the helper stays on and the automation
checks every 15 minutes and after a Home Assistant restart. An unavailable
calendar produces no commands. Only this next trip is selected: recurring
calendar events do not enable another plan automatically. An existing future
**Next departure** is preserved; cancel it before selecting a different trip.
Calendar edits or deletions after selection do not change the copied time.
Use **Clear departure time**, then turn the helper on again to select an
updated event. Turning the helper off only stops the search; it does not cancel
an already selected plan.

The built-in **Use next calendar event** button is an alternative for an
immediate, single lookup using the calendar configured in integration options.
Manual entry, the configured calendar and an optional vehicle adapter can
coexist. **Use vehicle departure** copies one upcoming timer from the selected
vehicle's locally published states. The adapter currently supports BavarianData;
see [vehicle timer setup](user-guide.md#charge-for-the-next-departure).
It does not fetch cloud data or start a repeating HA plan. A separately
configured dated departure source keeps its current timestamp from overriding
an explicit selection; when that source changes to another future timestamp,
it can select the new trip.

## Start vehicle climate before departure

This optional companion works with any selected **Next departure**, including
one copied from a calendar or a vehicle timer. Reaching the charging target early
keeps the departure time available until departure, so climate can still start.
The vehicle integration must provide a working climate-start action or script.
Reading a vehicle timer does not provide a remote climate-start command; select an
action only if your vehicle integration supplies one.

1. Create a **Text** helper with the default length, for example **Last climate
   departure**. Reserve it for this automation. For a helper configured in YAML, omit `initial` so Home Assistant restores its
   previous value after restarting.
2. Create an automation from **Precondition before the next trip**. Select the
   same wallbox departure entity, the helper and your vehicle's **start climate**
   action or script. Use an explicit start action rather than a toggle.
3. Select the lead time; the default is **15 minutes**. Leave the automation
   enabled for climate on future trips, or disable it whenever climate is not
   wanted.

The automation checks every minute. After a Home Assistant restart it catches
up if the departure is still ahead and already inside the lead window. It
records the selected departure in the helper **before** attempting your actions,
using an unambiguous timestamp even when clocks change. Normal restarts do not
repeat them. An action failure is not retried indefinitely: check the automation trace and start climate manually if needed.
A restart during the action can leave that attempt incomplete. To retry through
this automation, clear the Text helper while departure is still ahead;
the next check can then run it again. Changing the departure to a different time
counts as a new trip and can start climate again.

The climate action does not bypass SoC or energy limits or directly change the
wallbox pause switch. An active timed charging plan may resume charging while
below its target. Once a charge limit is reached, it remains authoritative.
Whether the vehicle draws climate energy from the grid depends on the vehicle
and its current charging state; this automation cannot guarantee grid power.
Avoid a second vehicle schedule that also starts climate for the same trip.

## Set up PV charging

Use the two PV blueprints together if you want both current adjustment and
automatic start/stop. The current blueprint alone keeps at least 6 A and can
therefore draw from the grid when the available solar power is too low.

1. Choose a power sensor in **W or kW**, positive for available surplus.
   It must report the **total power budget available to the wallbox**, including
   power the vehicle already consumes. Raw grid export alone falls as charging
   increases; using it directly can make the current oscillate. Verify your
   sensor's definition before enabling the automation.
2. For **Charge from PV surplus**, select **Charging current limit**,
   **Charging paused**, **Vehicle connected** and **Requested phases** (whose `switched` attribute is the wallbox's answer; before 0.9.0 this was the **Wallbox phase selection** sensor) from
   the same wallbox. Set the maximum current to suit the installation. The
   automation also respects the number entity's configured maximum.
3. Check the phase assumption: `single_phase` divides the power by one phase;
   `all_available` divides it by three. This blueprint assumes a three-phase
   installation and does not detect how many phases your vehicle actually uses.
4. For **Pause and resume on PV surplus**, select the same power sensor and
   pause switch. Match the thresholds to your chosen phase count and minimum
   current; the defaults are for **one phase**, not three.

At 230 V and a minimum of 6 A, these are example thresholds:

| Charging configuration | Pause below | Resume above |
|---|---:|---:|
| One phase | 1,380 W | 1,700 W |
| Three phases | 4,140 W | 5,000 W |

Threshold inputs are always in **watts**, including when the sensor uses kW.
Keep the resume threshold higher than the pause threshold. A higher minimum
current needs correspondingly higher thresholds. The default hold time is
six minutes; keep it longer than five minutes. These examples do not switch
phases automatically.

Since 0.5.2b2, the bus owner's configurable state-change interval adds a
separate guard (default 300 seconds). It does not replace these power thresholds
or their hold times. Early pause and resume requests can be queued; a later
pause replaces the pending resume and also waits for its due time. See
[Waiting between charging changes](user-guide.md#waiting-between-charging-changes).

## What to expect

- The current is recalculated when surplus or selected phases change, when a
  vehicle connects, and when the pause switch turns off. A stable surplus
  therefore also applies after resuming. Resume first restores the remembered
  setpoint; the recalculated value follows at the next permitted write. The
  integration still enforces the five-second current-write interval.
- A newer valid power budget replaces a still-running current adjustment.
  The latest value is applied at the next permitted write; a slow response
  does not silently discard it. Cancellation retains the write cooldown.
- **Charging paused: on** prevents the current blueprint from writing. To
  keep a manual pause, also disable the pause/resume automation; that
  automation is explicitly allowed to resume when its threshold is met.
- Missing or invalid power values, or a unit other than W/kW, produce no new
  commands. They do **not** pause charging automatically. The last setting
  remains in effect while the active service or integration keeps sending its
  heartbeat. A service continues through HA Core restarts, but the blueprint
  cannot calculate new values while HA is offline.
- An unavailable current control, pause switch or phase sensor prevents
  current adjustment. An unknown initial current setpoint can still be set
  from a valid power budget when the current control is available.
- Home Assistant resets pending hold timers when it restarts or reloads
  automations. The thresholds must become true again to trigger a new timer;
  this is not a persistent charging schedule. See the
  [Home Assistant trigger documentation](https://www.home-assistant.io/docs/automation/trigger/#template-trigger).
- Avoid competing automations that write the current or pause switch. A
  tariff schedule, manual command or phase change can conflict with PV control.

These are convenience automations, not house-connection overload protection.
Review [Safety and limitations](safety.md) and test the behavior with your
vehicle before leaving an automation unattended.

## Updating existing blueprint automations

When upgrading from **0.2.1 or earlier**, open each existing **Charge from PV
surplus** automation and select the **Charging paused** input added in 0.2.2. Until you
select it, that automation deliberately sends no current commands. Existing
automations still load. Review the power sensor, phase assumption and thresholds
above; re-save your selections and reload automations after the blueprint files
have been updated. The six-minute hold-time default applies unless you saved
an explicit value previously.

For **0.2.3**, reload existing PV surplus automations after installing the
updated blueprint to use the latest surplus value during overlapping updates.
Existing 0.2.2 input selections remain valid.

The integration updates unchanged bundled files on setup. It preserves files
you have edited, so those copies do **not** receive these fixes automatically.
Back up custom edits, compare with the updated bundled blueprint and apply the
changes to your copy. Prefer a separate filename for custom blueprints.

Blueprint files live in
`config/blueprints/automation/mennekes_amtron_rtu/`.

For tariff schedules and action examples, see [Automations](automations.md).
