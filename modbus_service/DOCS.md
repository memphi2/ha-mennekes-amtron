# Mennekes Amtron Modbus Service

Keep the wallbox heartbeat running through Home Assistant Core restarts.
This optional app is the preferred way to operate the integration: it holds
the last charging state, including a deliberate pause, while HA is offline.

**Beta 0.5.0b5 · amd64 and aarch64 · separate integration required**

[First setup](#first-setup) · [Daily use](#daily-use) ·
[Updates](#updates) · [Help](#help)

## First setup

Install the **0.5.0b5 Mennekes Amtron RTU integration** first and restart Home
Assistant Core. Enable **Pre-release** in the Marketplace on HA 2026.11+, or beta
releases in HACS on older supported versions. The integration and this app are
separate installations.

1. **Prepare an existing wallbox.** Note its pause, current limit, resume current
   and charging-release settings. Disconnect the vehicle for the first handover.
   Disable the existing direct-serial integration entry to release the adapter;
   keep the entry so its device and entity IDs are retained.
2. **Configure the app.** Select the adapter and match the wallbox's address,
   baud rate and frame. Leave **Read-only on** initially; see the table below.
3. **Start the app.** Enable **Start on boot** and **Watchdog**. Check the log for
   its serial connection. Read-only mode deliberately leaves the heartbeat off.
4. **Connect HA.** Under **Settings → Devices & services**, configure the
   discovered **Mennekes Amtron** entry. Address and token are automatic; there is
   nothing to copy. The existing entry is reused for the same wallbox. Re-enable
   it if it remains disabled, then compare readings with the wallbox.
5. **Enable control.** Set the permitted current cap, turn **Read-only off in the
   app**, save and restart the app. Its settings are read at startup. HA enables
   its controls after fresh writable status; there is no second HA write switch
   and no HA restart is needed. The app restart briefly interrupts its heartbeat.
6. **Check the handover.** Reapply and verify the noted pause/current/release
   settings before reconnecting the vehicle. Test a deliberate pause and an HA
   Core restart: the app should stay running and the pause should remain active.

| App option | Initial setting |
|---|---|
| Serial adapter | Your USB RS-485 adapter; prefer `/dev/serial/by-id/...`. |
| Wallbox address | Match the wallbox; factory default **50**. |
| Baud rate / frame | Match the wallbox; factory defaults **57600 / 8n2**. |
| Read-only | **On** until readings are checked. |
| Current cap | Default **6 A**; set within your installation's permitted current. |
| Status / measurement interval | Keep **5 / 30 seconds** initially. |

Leave network settings at their defaults. No host port needs publishing and no
token needs configuring. Only this app may own the serial adapter, even when
read-only.

On first installation, the app reads the wallbox's current state. If fallback
changed that state during handover, it cannot infer your earlier intention.
A paused wallbox without saved app state uses **6 A** as its first resume target.

## Daily use

- **Pause/resume:** use **Charging paused** in HA.
- **Current limit:** use the HA slider. **0 A removes the EMS limit; it does not
  pause charging.** The app and HA caps constrain limited-current commands;
  the wallbox's hardware limits apply even at 0 A.
- **Write permission:** change the app's **Read-only** switch, save and restart
  the app. No HA restart is needed. HA controls remain registered but unavailable
  while read-only or while the app is not ready. HA does not send old commands
  when permission changes. The service may still recover error 200 automatically
  by reasserting its saved state, including a deliberate pause.
- **Heartbeat status:** check HA's **Heartbeat** diagnostic sensor. It records
  health changes rather than every beat.

HA can remain offline indefinitely while the app holds the last charging state.
Automations cannot produce new settings during that outage. **App, host, power
or USB failures still interrupt the heartbeat** and use the wallbox's configured
fallback. Switching the app to read-only also stops the heartbeat.

To give the serial bus to another master, stop the app. Disabling the HA entry
or selecting read-only does not release it. Include app data in backups; it
contains the saved charging intent and private access token. Saved settings
replace a small file rather than accumulating a history.

## Updates

Update the integration first and restart Home Assistant Core; the running app
continues its heartbeat. Use **Check for updates** in the app store to obtain the
separate app update. **Updating this app briefly interrupts the heartbeat.**

From beta 4, the existing **0.5.0b4** app already supplies the status needed by
the **0.5.0b5** integration. HA controls therefore follow its **Read-only** switch
before you update the app. Keep the existing entry; no new setup is needed.

The beta repository ends in `#apps-beta`; the stable repository uses `#apps`.
Supervisor treats them as different apps. To change channels, back up the data,
copy the options, stop the old app before starting the new one, and confirm its
discovery. Keep the old app and backup until the handover is verified.

## Help

| Symptom | What to check |
|---|---|
| No discovered entry | Confirm the app is running. In HA, choose **Add integration → Mennekes Amtron RTU → Connect to the Modbus service** to look it up again. |
| Not ready / token rejected | Check the app log and retry discovery confirmation; HA retrieves the current credentials. |
| Controls unavailable | Check **Read-only**. After changing it, save and restart the app, then wait for fresh status. Check the serial connection if controls remain unavailable. |
| App stops immediately | The log's **Invalid or unreadable service configuration** message names the invalid setting or startup phase. |
| Heartbeat or write fault | Download integration diagnostics before restarting the app, then check the log. |

The log shows service startup, the serial connection and heartbeat start/stop.
Diagnostics include the last 20 failed write/recovery records in RAM, with timing
information and no packet contents or credentials. They add no Recorder rows and
are lost when the app restarts.

This beta still needs hardware testing, including deliberate pause, HA restart,
app restart and reconnection. Error-200 recovery while paused needs confirmation
on the wallbox firmware.

[Full setup, fallback and troubleshooting guide](https://github.com/memphi2/ha-mennekes-amtron/blob/v0.5.0b5/docs/modbus-service.md)
