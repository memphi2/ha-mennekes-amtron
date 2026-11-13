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
defaults to six minutes. Early pause/resume, phase,
release and lock changes are queued using the configurable state-change
interval (default 300 seconds). A newer charging request replaces older pending
work. A stop replaces a pending start and also waits for its due time. See
[Waiting between charging changes](user-guide.md#waiting-between-charging-changes).

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
It does not restore the schedule when HA starts later. A recent
pause can delay an accepted start: inspect **Pending change** and **Pending
change due**, as well as the **automation trace**. A running service can execute an already queued
command while HA is offline. Check the current state after a restart rather
than assuming the schedule ran.

When using `automations.yaml` directly instead of the UI editor, add this as
one list item. Do not add a second `automation:` section to configuration.yaml.

## Change phases deliberately

Select **Requested phases** on the device page before automating a change.
The integration pauses first when the wallbox reports charging and leaves it
paused. Resume separately after the waiting interval, once the vehicle and
wallbox are ready. The PV blueprints do not coordinate dynamic phase switching.
The phase command waits for live pause confirmation and the
configured extra wait, then checks the state again. A failed confirmation leaves
the phase command unsent and retains the pause.
See [Phase switching](user-guide.md#phase-switching).

## Recovery and notifications

Error 200 is recovered automatically when writes are enabled and the heartbeat
is healthy. The service handles this independently of HA. An automation from
the **Recover from a lost heartbeat** blueprint is not needed and only adds
duplicate attempts. Its button action preserves a deliberate pause. If the error returns, investigate
[heartbeat failures](troubleshooting.md#error-200-energy-manager-unavailable).
Repeated recovery is not a substitute for a functioning connection.

Use **Notify on a grid-operator downgrade** to see when a hardware restriction
starts and ends. The integration observes that restriction; it cannot remove it.

## React to wallbox events

**Wallbox event** reports what the wallbox did on its own: a car plugged in or
unplugged, a charge that started or stopped, a fault that appeared or cleared,
an RFID decision, a downgrade, a cable lock or a phase switch. It is enabled by
default and creates no entry at startup; see the
[event meanings](entities.md#wallbox-events). Use a state trigger on the entity
and filter its `event_type` attribute; the attributes hold the values of that
moment, for example the energy of the session that just ended.

This example reports the charged energy when the car is unplugged and raises a
notification for a wallbox fault. Replace the entity ID with your actual
**Wallbox event** entity.

```yaml
alias: Wallbox reports
triggers:
  - trigger: state
    entity_id: event.mennekes_amtron_wallbox_event
    not_from:
      - unknown
      - unavailable
    not_to:
      - unknown
      - unavailable
conditions: []
actions:
  - choose:
      - conditions:
          - condition: template
            value_template: >-
              {{ trigger.to_state.attributes.event_type == 'vehicle_unplugged' }}
        sequence:
          - action: notify.notify
            data:
              message: >-
                Charged {{ trigger.to_state.attributes.session_energy | round(1) }} kWh.
      - conditions:
          - condition: template
            value_template: >-
              {{ trigger.to_state.attributes.event_type == 'error_occurred' }}
        sequence:
          - action: persistent_notification.create
            data:
              notification_id: wallbox_error
              title: Wallbox error
              message: >-
                Error {{ trigger.to_state.attributes.error_code }}
                ({{ trigger.to_state.attributes.error_category }}).
mode: queued
```

A reached charge target also ends in `charging_stopped`; the energy target's
own stop is reported by **Control result** as well. Use `mode: queued` so that
a `charging_stopped` and a `vehicle_unplugged` from the same poll are both handled.

## React to command results

Enable **Control result** under **Settings → Devices & services → Entities**
first; it starts disabled to avoid history growth from frequent current commands.
You can [exclude it from Recorder](recorder.md#recorder-defaults)
while keeping live automation triggers. Use it to notify you about a rejected command, an uncertain
write, a phase-pause timeout or a recovery that did not clear the error. In the
UI, add a state trigger for the event entity, then filter its `result`
attribute; `event_type` names the command as well, for example `pause_failed`. This follows Home Assistant's [event-entity pattern](https://www.home-assistant.io/integrations/event/).
Use a state trigger, not a raw event-bus trigger.

This example excludes startup/restored state and availability-only changes.
Replace the entity ID with your actual **Control result** entity. The action
and reason in the message are stable automation values, so they work in every
UI language. Read the [event meanings](entities.md#command-events)
before using an outcome to issue another command.

```yaml
alias: Wallbox command needs attention
triggers:
  - trigger: state
    entity_id: event.mennekes_amtron_control_result
conditions:
  - condition: template
    value_template: >-
      {{ trigger.from_state is not none
         and trigger.to_state is not none
         and trigger.from_state.state != 'unavailable'
         and not trigger.from_state.attributes.get('restored', false)
         and trigger.to_state.state not in ['unknown', 'unavailable']
         and trigger.to_state.state != trigger.from_state.state
         and trigger.to_state.attributes.result
             in ['failed', 'uncertain', 'recovery_failed'] }}
actions:
  - action: persistent_notification.create
    data:
      notification_id: wallbox_command_attention
      title: Wallbox command needs attention
      message: >-
        {{ trigger.to_state.attributes.action }}:
        {{ trigger.to_state.attributes.reason }}.
        Check the wallbox status before retrying.
mode: queued
max: 10
```

The feed does not replay outcomes missed while HA was
disconnected. Use current state to reconcile after reconnect; this event entity
is not a persistent command journal. A service must support the new optional
feed; earlier compatible services expose the event entity as unavailable.

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
