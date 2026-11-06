# Automations

Start with the [bundled blueprints](blueprints.md) for PV charging and
downgrade notifications. They appear in Home Assistant
after you add the wallbox and need no YAML editing.

Use one control strategy at a time. A PV automation and a tariff schedule can
otherwise undo each other's pause or current setting. Enable writes with the
app's **Read-only** switch off in service mode, saving and restarting the app,
or **Modbus master** in the integration options for a direct USB or LAN connection.
Current writes retain a **five-second minimum interval**. For automatic
pause/resume, use different start/stop thresholds and hold times; the PV blueprint
defaults to six minutes. **Next beta, unreleased:** early starts and phase
changes are queued using the configurable state-change interval (default
300 seconds). A newer charging request replaces older pending work, and a stop
clears a pending start. See [Waiting between charging changes](user-guide.md#waiting-between-charging-changes--next-beta-unreleased).
Published 0.5.2b1 still rejects early pause/resume and phase requests rather than
retrying them automatically.

## PV charging

Follow the [PV setup instructions](blueprints.md#set-up-pv-charging). They
cover the required power-budget sensor, W/kW units, phase assumptions and
thresholds. The current blueprint respects the pause switch; the pause/resume
blueprint is allowed to turn it off again. Disable automatic resume if you
want a manual pause to remain in effect.

Missing power data does not automatically stop charging. The last current
setting remains while the active service or integration keeps its heartbeat
running. These recipes
are not house-connection overload protection. In particular, never turn an
insufficient current budget into a 6 A request merely by clamping it upwards.
The wallbox's static limit does not account for other household loads.

## Charge during a fixed tariff window

This example requests 10 A at 22:00 and pauses at 06:00. Choose a current that
suits your installation. Limited-current commands respect the HA entry cap and,
in service mode, the app or standalone service cap; the lower applicable cap
wins. A current command can resume a paused charge after the waiting interval.

Create an empty automation, open its **Edit in YAML** editor and paste the
following. Replace both entity IDs with your own. The IDs shown assume the
default device name and English entities.

```yaml
alias: Wallbox night charging
triggers:
  - trigger: time
    at: "22:00:00"
    id: start
  - trigger: time
    at: "06:00:00"
    id: stop
conditions: []
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: start
        sequence:
          - action: number.set_value
            target:
              entity_id: number.mennekes_amtron_charging_current_limit
            data:
              value: 10
      - conditions:
          - condition: trigger
            id: stop
          - condition: state
            entity_id: switch.mennekes_amtron_charging_paused
            state: "off"
        sequence:
          - action: switch.turn_on
            target:
              entity_id: switch.mennekes_amtron_charging_paused
mode: single
```

A fixed time trigger is missed if Home Assistant is stopped at that time.
It does not restore the schedule when HA starts later. In the next beta, a recent
pause can delay an accepted start: inspect **Pending change** and **Pending
change due**, as well as the **automation trace**. In published 0.5.2b1 the early
start is rejected instead. A running service can execute an already queued
command while HA is offline. Check the current state after a restart rather
than assuming the schedule ran.

When using `automations.yaml` directly instead of the UI editor, add this as
one list item. Do not add a second `automation:` section to configuration.yaml.

## Change phases deliberately

Select **Requested phases** on the device page before automating a change.
The integration pauses first when the wallbox reports charging and leaves it
paused. Resume separately after the waiting interval, once the vehicle and
wallbox are ready. The PV blueprints do not coordinate dynamic phase switching.
See [Phase switching](user-guide.md#phase-switching).

## Recovery and notifications

Error 200 is recovered automatically when writes are enabled and the heartbeat
is healthy. The service handles this independently of HA. Disable any older
**Recover from a lost heartbeat** automation to avoid duplicate attempts.
The blueprint remains available for existing installations. Its button action
preserves a deliberate pause. If the error returns, investigate
[heartbeat failures](troubleshooting.md#error-200-energy-manager-unavailable).
Repeated recovery is not a substitute for a functioning connection.

Use **Notify on a grid-operator downgrade** to see when a hardware restriction
starts and ends. The integration observes that restriction; it cannot remove it.

## Call the current action

For a normal current limit, use `number.set_value` as in the schedule above.
For device-based calls or the explicit unlimited option, see
[Action fields and an example](user-guide.md#actions). Writing `current: 0`
means **no energy-manager limit**, not pause.

## Record charging energy

Add **Total energy** to the Energy dashboard's individual devices. The default
English ID is `sensor.mennekes_amtron_total_energy`; check your actual entity.
Keep this sensor recorded. It is **not usable for billing**. See
[Recorder](recorder.md) before excluding sensors from history.
