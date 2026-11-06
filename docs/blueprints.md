# Ready-made automations

The integration includes four blueprints. Adding the wallbox installs them
under **Settings → Automations & scenes → Blueprints**. Choose a blueprint,
select **Create automation**, then choose the entities for the same wallbox.
The PV sensor comes from your inverter or energy-management setup.

| Blueprint | Purpose |
|---|---|
| **Charge from PV surplus** | Adjust the current while a vehicle is connected and charging is not paused |
| **Pause and resume on PV surplus** | Pause below one power threshold and resume above a higher one, after a hold time |
| **Recover from a lost heartbeat** | Legacy manual-button automation; recovery is now built in. Disable existing instances to avoid duplicate attempts. |
| **Notify on a grid-operator downgrade** | Notify when a downgrade starts and ends |

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
   **Charging paused**, **Vehicle connected** and **Switched phases** from
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

In version 0.5.2b2, the bus owner's configurable state-change interval adds a
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
