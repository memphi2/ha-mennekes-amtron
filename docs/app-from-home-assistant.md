# Use the app from Home Assistant

With the [Modbus service app](modbus-service.md), charging jobs, boost, guest
charging and weekly plans can be started and stopped from Home Assistant:
from a dashboard with one tap, or from an automation. The app's
[web pages](app-guide.md) keep working beside it and show the same state.

Departure planning needs nothing from this page; it already lives in Home
Assistant, see [Departure planning](departure-planning.md).

## What you get

| In Home Assistant | What it does |
|---|---|
| `sensor.*_charging_job` | State of the app's charging job or guest charge, or **No job**. Attributes: `kind`, `start`, `finish`, `current`, `energy`, `delivered`, `reserve`, `reason`, `boost_until`, `boost_available`. |
| `sensor.*_weekly_plan` | **Active** or **Not active**. Attributes: `plan` and `until` while active, and `plans` with the names of the plans saved in the app. |
| Action `start_charging_job` | Charge in a time window with a fixed current up to an amount of energy. |
| Action `start_guest_charging` | Charge a guest's car from now on, limited in time and energy. |
| Action `stop_charging_job` | Stop the running job or guest charge. Charging pauses and does not resume by itself. |
| Action `clear_charging_job` | Remove an ended or stopped job so that a new one can start. |
| Action `start_boost` / `cancel_boost` | Charge right away although the job starts later, then return to the plan. |
| Action `activate_weekly_plan` / `deactivate_weekly_plan` | Switch a weekly plan of the app on for the plugged-in car, or off. |

All actions are named `mennekes_amtron_rtu.<action>`. Both sensors exist only
with the app; with a direct USB or LAN connection the actions answer that they
need the app.

Weekly plans themselves — weekdays, departure time, current and energy — are
created and changed on the app's **Weekly plans** page. Home Assistant
activates a saved plan by its vehicle name.

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
| `activate_weekly_plan` | `plan` | The plan's vehicle name as the app shows it. Required. |

A time without a time zone is taken as local time.

## One tap, and what happens behind it

On its own pages the app first shows a preview of an order and asks you to
confirm it: the preview holds the checked values and is tied to the current
plug-in, so that an order cannot reach another car or an outdated state.

An action from Home Assistant asks for the same preview and confirms it at
once. Nothing is skipped: if the car was unplugged, another order is running
or a value is not allowed, the action fails with the app's reason and nothing
is started. For the question "are you sure?", give the dashboard button a
confirmation, as in the examples below. Automations call the same actions
without a question.

## Dashboard with Mushroom cards

The view below uses the third-party
[Mushroom](https://github.com/piitaya/lovelace-mushroom) cards. Replace
`amtron` with your device's entity prefix. Add it under **Edit dashboard →
Add view → Edit in YAML**, or copy single cards.

```yaml
title: Wallbox
icon: mdi:ev-station
cards:
  - type: custom:mushroom-title-card
    title: Wallbox
  - type: custom:mushroom-template-card
    entity: sensor.amtron_charging_control
    icon: mdi:ev-station
    icon_color: >-
      {{ 'green' if is_state('sensor.amtron_charging_wait_reason', 'none')
         else 'red' if is_state('sensor.amtron_charging_control', 'unavailable')
         else 'orange' }}
    primary: "{{ state_translated('sensor.amtron_charging_control') }}"
    secondary: "{{ state_translated('sensor.amtron_charging_wait_reason') }}"
    multiline_secondary: true
    tap_action:
      action: more-info

  - type: custom:mushroom-title-card
    subtitle: Charging job
  - type: custom:mushroom-template-card
    entity: sensor.amtron_charging_job
    icon: mdi:clipboard-clock-outline
    icon_color: >-
      {{ 'green' if is_state('sensor.amtron_charging_job', 'charging')
         else 'blue' if is_state('sensor.amtron_charging_job', 'scheduled')
         else 'grey' }}
    primary: "{{ state_translated('sensor.amtron_charging_job') }}"
    secondary: >-
      {%- set s = 'sensor.amtron_charging_job' -%}
      {%- if not is_state(s, 'none') and state_attr(s, 'finish') -%}
      {{ state_attr(s, 'current') }} A · {{ state_attr(s, 'delivered') | float(0) | round(1) }}
      / {{ state_attr(s, 'energy') }} kWh · until
      {{ state_attr(s, 'finish') | as_datetime | as_local | as_timestamp | timestamp_custom('%a %H:%M') }}
      {%- endif -%}
    multiline_secondary: true
    tap_action:
      action: more-info
  - type: custom:mushroom-chips-card
    chips:
      - type: template
        icon: mdi:play
        content: Start
        tap_action:
          action: perform-action
          perform_action: script.amtron_start_charging_job
          confirmation:
            text: Start the charging job with the values below?
      - type: template
        icon: mdi:rocket-launch-outline
        content: Boost 30 min
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.start_boost
          data:
            duration: 30
          confirmation:
            text: Charge now for 30 minutes, then return to the plan?
      - type: template
        icon: mdi:rocket-outline
        content: End boost
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.cancel_boost
      - type: template
        icon: mdi:stop-circle-outline
        content: Stop
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.stop_charging_job
          confirmation:
            text: Stop the charging job? Charging pauses.
      - type: template
        icon: mdi:clipboard-remove-outline
        content: Clear
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.clear_charging_job
  - type: custom:mushroom-number-card
    entity: input_number.amtron_job_current
    display_mode: buttons
  - type: custom:mushroom-number-card
    entity: input_number.amtron_job_energy
    display_mode: buttons
  - type: entities
    entities:
      - entity: input_datetime.amtron_job_finish

  - type: custom:mushroom-title-card
    subtitle: Guest charging
  - type: custom:mushroom-chips-card
    chips:
      - type: template
        icon: mdi:account-clock-outline
        content: 2 h · 10 kWh
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.start_guest_charging
          data:
            duration: 120
            current: 11
            energy: 10
          confirmation:
            text: Charge the guest's car for up to 2 hours and 10 kWh?
      - type: template
        icon: mdi:account-clock-outline
        content: 6 h · 30 kWh
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.start_guest_charging
          data:
            duration: 360
            current: 11
            energy: 30
          confirmation:
            text: Charge the guest's car for up to 6 hours and 30 kWh?

  - type: custom:mushroom-title-card
    subtitle: Weekly plan
  - type: custom:mushroom-template-card
    entity: sensor.amtron_weekly_plan
    icon: mdi:calendar-week
    icon_color: "{{ 'green' if is_state('sensor.amtron_weekly_plan', 'active') else 'grey' }}"
    primary: "{{ state_translated('sensor.amtron_weekly_plan') }}"
    secondary: >-
      {{ state_attr('sensor.amtron_weekly_plan', 'plan')
         or (state_attr('sensor.amtron_weekly_plan', 'plans') or []) | join(', ') }}
    tap_action:
      action: more-info
  - type: custom:mushroom-chips-card
    chips:
      - type: template
        icon: mdi:calendar-check
        content: Activate
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.activate_weekly_plan
          data:
            plan: Family car
          confirmation:
            text: Activate the weekly plan for the plugged-in car?
      - type: template
        icon: mdi:calendar-remove
        content: Deactivate
        tap_action:
          action: perform-action
          perform_action: mennekes_amtron_rtu.deactivate_weekly_plan
          confirmation:
            text: Deactivate the weekly plan and stop its charging job?

  - type: custom:mushroom-title-card
    subtitle: Departure
  - type: entities
    entities:
      - entity: datetime.amtron_next_departure
      - entity: sensor.amtron_timed_charging_status
      - entity: button.amtron_clear_departure_time
```

Replace `Family car` with the vehicle name of your weekly plan, and change the
guest presets to what suits you. The departure card needs
[departure planning](departure-planning.md); leave it out otherwise.

### Helpers and script for the Start button

A dashboard button cannot read values from other cards, so **Start** calls a
small script that takes the end time, current and energy from three helpers.
Add this to `configuration.yaml`, or create the same helpers and script under
**Settings → Devices & services → Helpers** and **Settings → Automations &
scenes → Scripts**:

```yaml
input_number:
  amtron_job_current:
    name: Job current
    min: 6
    max: 16
    step: 1
    unit_of_measurement: A
    icon: mdi:current-ac
  amtron_job_energy:
    name: Job energy
    min: 1
    max: 80
    step: 1
    unit_of_measurement: kWh
    icon: mdi:battery-charging-medium

input_datetime:
  amtron_job_finish:
    name: Job end
    has_date: true
    has_time: true

script:
  amtron_start_charging_job:
    alias: Wallbox start charging job
    sequence:
      - action: mennekes_amtron_rtu.start_charging_job
        data:
          finish: "{{ states('input_datetime.amtron_job_finish') }}"
          current: "{{ states('input_number.amtron_job_current') | float }}"
          energy: "{{ states('input_number.amtron_job_energy') | float }}"
```

Set `max` of the current helper to your app's current cap.

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
