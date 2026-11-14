# Your first charge

[Guide home](README.md) · [Installation](installation.md) · [Everyday charging](user-guide.md) · [Help](troubleshooting.md)

Use this check after the wallbox appears in Home Assistant and its readings are
correct. If it is not connected yet, complete [Installation](installation.md)
first. Leave vehicle recognition, Plug & Charge and charging automations off
until this basic check works.

## Check readings and enable controls

Keep the car disconnected while checking the initial settings. Confirm that the
reported device and readings match your wallbox. Choose an appropriate current
cap and enable writes in **one** place:

| Your connection | Enable controls here |
|---|---|
| Service app | App **Configuration → Read-only** off, save and restart the app |
| Direct USB or LAN | Integration **Configure → Wallbox → Current limit and polling → Control mode: Modbus master** |

In service mode, HA receives the app's write permission automatically. There is
no second HA write switch. Check **Connection quality**: its `heartbeat`
attribute should become `ok`. Resolve any repair before continuing.

## First charge

1. Set **Charging current limit** to **6 A**. Make sure **Wallbox locked** is off
   and **Charging release** is on.
2. Plug in the car and complete any authorization the wallbox itself requires.
   Turn **Charging paused** off if necessary.
3. Check **Total power**. **Signaled current** is the offer to the car; the car
   may draw less or refuse charging because of its own settings.
4. Turn **Charging paused** on. Wait until the pause is confirmed.
5. Turn it off again and check that charging resumes at the remembered current.
   Then choose your usual current limit.

**0 A removes the software current limit; it is not a pause.** Use the pause
switch to stop charging.

A request can wait for the configured state-change interval (default 300 seconds).
Check **Pending change** and its due time. Repeated clicks or app restarts do not
make a waiting change safer or faster. [Waits and cancellation](user-guide.md#waiting-between-charging-changes).

## Check your restart behavior

With the service app, deliberately pause charging, restart **HA Core**, and check
that the app stays running and the pause remains. Restarting the app or host is
different: it interrupts the heartbeat and the wallbox fallback can apply.
[Restart behavior](user-guide.md#heartbeat-and-home-assistant-restarts).

## Next steps

- [Everyday controls and limits](user-guide.md)
- [Add a dashboard](app-from-home-assistant.md#dashboard-with-mushroom-cards)
- [Optional vehicle data](vehicle-setup.md)
- [A request did not work](troubleshooting.md#the-charging-current-does-not-follow)

## Optional command-line bus check

Normal setup needs no shell. For advanced troubleshooting only, see the
[read-only bus check](troubleshooting.md#optional-command-line-bus-check).
Stop the existing bus owner before running it.
