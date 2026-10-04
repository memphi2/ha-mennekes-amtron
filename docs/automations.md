# Automations

Working recipes. Replace `amtron` with your device's entity prefix, and read
[safety.md](safety.md) before you automate anything that writes.

Two rules the manufacturer sets, which every recipe below respects:

- change the charging current at most once every **5 seconds**,
- keep pause, resume and phase switches more than **5 minutes** apart, with
  hysteresis.

The integration enforces both: a faster current change is deferred and written
when the interval has passed, a faster pause or phase switch is refused with
an explanatory error.

## Charge from PV surplus

The core of solar charging: turn surplus watts into amps, never below 6 A,
never above what the wallbox allows.

```yaml
automation:
  - alias: Wallbox follows PV surplus
    triggers:
      - trigger: state
        entity_id: sensor.pv_surplus_power
    conditions:
      - condition: state
        entity_id: binary_sensor.amtron_vehicle_connected
        state: "on"
      - condition: state
        entity_id: switch.amtron_charging_paused
        state: "off"
    actions:
      - action: number.set_value
        target:
          entity_id: number.amtron_charging_current_limit
        data:
          value: >-
            {% set phases = 3 %}
            {% set amps = states('sensor.pv_surplus_power') | float(0)
                          / 230 / phases %}
            {{ [[ amps | round(1), 6 ] | max,
                 state_attr('number.amtron_charging_current_limit', 'max')
                 | float(16) ] | min }}
    mode: single
```

`phases` has to match what the wallbox is actually using. If you have dynamic
phase switching, read it instead of hard-coding it:

```yaml
{% set phases = 1 if is_state('sensor.amtron_switched_phases', 'single_phase')
                else 3 %}
```

## Pause and resume with hysteresis

Two separate automations with `for:` are what keeps the wallbox from
oscillating.

```yaml
automation:
  - alias: Wallbox pauses without surplus
    triggers:
      - trigger: numeric_state
        entity_id: sensor.pv_surplus_power
        below: 1380          # 6 A on one phase at 230 V
        for: "00:05:00"
    conditions:
      - condition: state
        entity_id: switch.amtron_charging_paused
        state: "off"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.amtron_charging_paused

  - alias: Wallbox resumes with surplus
    triggers:
      - trigger: numeric_state
        entity_id: sensor.pv_surplus_power
        above: 1600          # above the pause threshold, deliberately
        for: "00:05:00"
    conditions:
      - condition: state
        entity_id: switch.amtron_charging_paused
        state: "on"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.amtron_charging_paused
```

Use the **Charging paused** switch, not **Charging release**: the release
switches the wallbox relay and wears it out.

## Dynamic load management

Give the wallbox whatever the house is not using, bounded by your main fuse.

```yaml
automation:
  - alias: Wallbox gets the remaining house current
    triggers:
      - trigger: time_pattern
        seconds: "/30"
    conditions:
      - condition: state
        entity_id: binary_sensor.amtron_vehicle_connected
        state: "on"
    actions:
      - action: number.set_value
        target:
          entity_id: number.amtron_charging_current_limit
        data:
          value: >-
            {% set main_fuse = 35 %}
            {% set house = states('sensor.house_current_max_phase') | float(0) %}
            {% set wallbox = states('sensor.amtron_current_l1') | float(0) %}
            {% set spare = main_fuse - (house - wallbox) - 2 %}
            {{ [[ spare | round(1), 6 ] | max, 16 ] | min }}
```

The wallbox clamps anything it is sent to its own configured maximum, so an
over-eager automation cannot exceed the installation limit. That is a safety
net, not a licence to send nonsense.

## Charge in the cheap tariff window

```yaml
automation:
  - alias: Wallbox charges at night
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.amtron_charging_paused
      - action: number.set_value
        target:
          entity_id: number.amtron_charging_current_limit
        data:
          value: 16

  - alias: Wallbox stops in the morning
    triggers:
      - trigger: time
        at: "06:00:00"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.amtron_charging_paused
```

## Switch to one phase for small surplus

Only on hardware that supports it, and only with a vehicle that tolerates it.
The integration pauses the charge before switching, as the manufacturer's own
sequence does.

```yaml
automation:
  - alias: Wallbox goes single phase on low surplus
    triggers:
      - trigger: numeric_state
        entity_id: sensor.pv_surplus_power
        below: 4000
        for: "00:10:00"
    conditions:
      - condition: state
        entity_id: binary_sensor.amtron_phase_switch_capable
        state: "on"
      - condition: not
        conditions:
          - condition: state
            entity_id: select.amtron_requested_phases
            state: single_phase
    actions:
      - action: select.select_option
        target:
          entity_id: select.amtron_requested_phases
        data:
          option: single_phase
```

## Notice a §14a downgrade

The grid operator's dimming is enforced in the wallbox hardware. You cannot
prevent it, but you can see it.

```yaml
automation:
  - alias: Tell me when the grid operator dims the wallbox
    triggers:
      - trigger: state
        entity_id: binary_sensor.amtron_downgrade_active
        to: "on"
    actions:
      - action: notify.persistent_notification
        data:
          title: Wallbox downgraded
          message: >-
            The grid operator limited the wallbox to
            {{ states('sensor.amtron_downgrade_current') }} A.
```

## Recover from a lost heartbeat automatically

The integration raises a fixable repair issue for this, and the button does
the same thing. Automating it is reasonable; automating it in a tight loop is
not.

```yaml
automation:
  - alias: Wallbox recovers from a lost heartbeat
    triggers:
      - trigger: state
        entity_id: sensor.amtron_error_code
        to: energy_manager_unavailable
        for: "00:01:00"
    actions:
      - action: button.press
        target:
          entity_id: button.amtron_recover_from_error
    mode: single
```

If this fires often, fix the bus instead; see
[troubleshooting.md](troubleshooting.md#error-200-energy-manager-unavailable).

## Feed the Energy dashboard

Add `sensor.amtron_energy_total` as a consumption source under
*Settings → Dashboards → Energy → Individual devices*. The manufacturer marks
that register as **not usable for billing**.
