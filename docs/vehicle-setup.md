# Set up your vehicle data

[Documentation](README.md) › Vehicle setup

Basic wallbox controls do not need a vehicle integration. Add vehicle data only
for a percentage target, a departure plan, charging-loss estimates or optional
automatic charging permission. Existing Home Assistant entities work with any
suitable vehicle integration; BavarianData
can also supply supported data from one selected BMW vehicle.

Every setting of a car is in its profile: open **Configure → Vehicles**, then
the profile.

## Choose the setup that fits

| Your setup | What to do |
|---|---|
| No vehicle integration | Use the current slider, pause switch and optional kWh target. No vehicle setup is needed. |
| One car with an existing SoC sensor | Add a profile, open it and choose that charge-level entity under **Charge level and charge limit**. Add a maximum-limit entity only if you want one. |
| One BavarianData car | Create one profile, select its vehicle once, then enable only the data you want to use. |
| Several cars | Create a profile for each car and select the connected car before charging. Each profile keeps its own optional settings and loss average. |
| Choose the car automatically | Configure each profile's detection signals, then choose **Automatic selection** in the **Active vehicle** entity. Manual selection always remains available. |
| Let a recognized car charge automatically | Configure its detection signals and enable [Virtual Plug & Charge](#optional-virtual-plug--charge) on the wallbox. |

A profile does not enable every feature at once:

| Optional choice | What it enables |
|---|---|
| Charge level | A percentage charging target |
| Vehicle charge limit | A maximum selectable percentage based on the car's own limit |
| Usable battery capacity | Planning current for a selected departure time |
| Vehicle departure timers | A button to copy the next supported vehicle climate timer; only when the car sends its timers |
| Vehicle charging history | Better matching for estimated charging losses |
| Cable and home signals | Optional selection of the active profile, and recognition for Virtual Plug & Charge |

## Vehicle profiles

Vehicle profiles keep the data and settings of different cars together. You can
configure up to eight profiles. Manual selection is the default; optional
[automatic selection](#optional-automatic-vehicle-selection) can use cable and
home signals. Adding a profile does not enable a percentage target, departure
planning or loss estimates.
Enable only the features you want for that profile.

Open **Configure → Vehicles → Add profile**. Give the profile
a name, choose **BavarianData**, then choose the vehicle device. Select the car
using **Active vehicle** on the wallbox device, or **Select vehicle** in the
**Vehicles** menu. The integration finds that vehicle's supported data through Home
Assistant's entity registry, so renamed entities do not require guessed entity
names. Each optional feature uses the same selected car. No BMW credentials or
additional cloud requests are needed.

BavarianData appears in this choice only
when Home Assistant has a supported vehicle device from that integration.
Manual entity sources remain available for other cars. If a saved integration
is temporarily absent, you can still rename its existing profile without losing
its vehicle binding or settings.

The charge level, the car's maximum charge limit, usable battery capacity,
departure timers and charging history are separate choices. A missing value
does not cause the integration to select another car. Unsupported or unavailable
data stays unavailable; manual sources remain an alternative.

Enable the corresponding vehicle entities in BavarianData if they are disabled.
The adapter reads the charge level, target charge level and maximum usable
battery energy. For the charge level it prefers BavarianData's **predicted**
charge level, which continues between the car's rare reports, and otherwise the
reported high-voltage battery charge level. It does not enable entities itself.
For capacity, it uses the car's reported usable energy when full, accounting for
its current battery condition; it does not substitute nominal battery size or a
battery-health percentage. If that value is missing, choose a suitable manual
capacity source instead.

Check the selected profile **before charging**, whether you chose it manually or
enabled automatic selection. Changing the active profile does not start
charging. Each car keeps its own upcoming departure: switching away remembers
it, and returning to the car restores it if it still lies ahead. A car without
a saved departure, or no vehicle at all, starts without one. Each profile
keeps its own percentage target: returning to a car restores its last target,
capped at its current limit, instead of no target. With no vehicle selected, no
percentage target applies. Charging-loss averages are kept separately for
each profile; changing its data source starts a new average for that source.
A charge that began before a manual profile selection is not newly assigned to it.
Automatic recognition can arrive later within the same observed connection,
as described below.
The profile's saved last result and average remain available when you return
to it.

### Everything for one car in its profile

**Configure → Vehicles → Open a profile’s settings** covers everything that
belongs to that car. Adding or opening a profile guides you
through these areas one after another instead of a menu for each. Each area
explains what it is for and is optional: **Next** skips it, and an area you set
up moves on to the next one. Nothing is saved before **Save and finish** or the
last area, the detection signals. Changes made meanwhile elsewhere, such as
another selected car, are kept. Each area names the entities it currently uses,
for example **In use: charge level sensor.i4_soc**.

When a profile is linked to a car in a vehicle integration, or its car changes,
it uses everything that car reports right away: charge level, charge limit,
usable battery capacity, charging history for losses and detection signals.
You do not choose those entities yourself; the guided setup shows them, and
you can switch any area to a manual entity. The charge limit is only taken
over while the car reports a value, because an empty limit would refuse every
percentage target; turn it on later in **Charge level**. Departure planning
stays off until you set it up:

| Item | What it is for |
|---|---|
| Name and vehicle source | Rename the profile or link it to the car in BavarianData, so its data is read automatically |
| Charge level and charge limit | Stop at a percentage target; needed for departure planning |
| Next departure | Start charging in time for a departure instead of at once |
| Charging losses | Compare wallbox energy with the energy that reached the battery |
| Detection signals | Cable and home signals for automatic selection and Virtual Plug & Charge |

You can open and change any profile, not only the selected car. Changing a car
that is not selected leaves the selected car's target and departure untouched.
Without profiles, **Vehicle settings without a profile** holds the shared charge
level, departure and loss settings instead.

Existing manually selected sources keep working after an update. They are not
silently replaced by a BavarianData vehicle, even when old departure and history
settings refer to different cars. When you start using profiles, those existing
settings are retained as a manual profile.

### Optional automatic vehicle selection

Automatic selection is **off by default** and separately configurable for each
profile. Open **Configure → Vehicles → Open a profile’s settings**, choose
**Next** up to **Detection signals** and choose:

- **Use the vehicle integration**: BavarianData supplies the vehicle's cable
  sensor and GPS tracker. The cable sensor is BavarianData's **Charging
  Cable**, which needs **BavarianData 0.9.30 or newer**: it names the plug
  reading that decided it and carries BMW's time for it, so it proves a fresh
  plug-in for every car, whichever plug field it streams. An older
  BavarianData offers no detection signals. Its position is compared with Home Assistant's Home
  zone. Existing enabled entities are used; no extra cloud requests or vehicle
  commands are sent.
- **Select Home Assistant entities**: choose two separate signals for this car.
  The cable source can be a binary sensor with `on` meaning connected, or a
  sensor with text states: the next step asks which of its states mean
  connected, for example `connected`. Every other state counts as unplugged,
  and `unknown` or `unavailable` prevents selection. The home
  source may be a binary sensor with `on` meaning at home, or a `person`/
  `device_tracker` with state `home`. Prefer a signal that follows the vehicle;
  a person's presence alone does not establish where the car is.
- **Detection disabled for this profile**: exclude this profile from automatic
  selection while keeping its manual selection and optional features.

After configuring the participating profiles, choose **Automatic selection**
in the **Active vehicle** selector on the wallbox device page. It is offered once
at least one profile has detection signals. This is the only place to switch
automatic selection on. Open the selector’s details
to see the currently selected profile. Selecting a particular car or no vehicle
manually turns the automatic mode off.

The integration selects a car only when exactly one configured profile reports
both **cable connected** and **at home**. A positive change must remain stable
for three seconds. The selected car stays selected after it is
unplugged or while its reports are stale or unknown. The selection ends when another
configured car reports itself connected and at home, or when the wallbox sees a
car while the selected one reports itself disconnected or away; no percentage
target then applies to the unknown car. A valid disconnected or away signal excludes that car, even
if its other signal is missing. For the remaining candidates, unknown or
unavailable signals block selection, as do signals not reported to Home Assistant
within 30 minutes. A parked car sends no new position, so its last known
position at home still counts when its cable signal switches to plugged in
within two minutes of the wallbox seeing a car. A signal that only repeats an
older plug-in does not count. A car plugged in long after parking is
therefore recognized; a fresh position elsewhere still excludes it.
No matching car or several matching cars means **no active vehicle**. The
previous car's target and departure plan stop applying when the assignment
changes; each car keeps them for its return. Detection alone never starts charging or lifts a pause. If the result is
unclear, choose the correct profile manually.

Changing the vehicle, manually or automatically, does not reload the
integration: the wallbox entities stay available throughout.
Vehicle entities, such as the vehicle charge target, the departure time and the
charging-loss sensors, exist as soon as any profile uses the feature. They are
unavailable while the selected car, or no car, does not use it.

These signals are an optional selection aid, not an identity supplied by the
wallbox. The 30-minute check concerns the latest **Home Assistant report**, which
cannot always establish when the vehicle originally measured its cable state
or position. Other integrations can report saved values again after restarting.
BavarianData can restore both the cable state and tracker coordinates; an exposed
BMW cable timestamp is checked, but missing timestamps cannot prove measurement
freshness. The GPS accuracy area must also fit fully inside or outside the Home
zone; a position crossing its boundary remains unknown. Check the selected car
before relying on its charge target or loss estimate.

The car may report its cable connection later than the wallbox. Automatic
recognition can still associate that same observed charging cycle with the car,
even if charging has already started. A conflicting vehicle selection or a gap
in connection observation prevents this association. With Virtual Plug & Charge
enabled, charging release waits for recognition. Saved loss results and averages
remain separate for each car.

### The car's rules apply only to that car

A profile with detection signals stays selected, manually or
automatically, while the car is away: you can set its charge target and
departure in advance. Its rules, i.e. the **Vehicle charge target**, the
departure plan and the learned phases, apply only after the car confirms the
plug-in. The car confirms it by reporting itself **plugged in at home after the
wallbox was last seen empty**. Until then:

- The departure plan shows **Car not confirmed**. A pause the plan set earlier
  ends, so another car is not held until a late start.
- The percentage target is not reached from this car's charge level. A target
  already reached is set again without a charge level, and the stop pause it
  caused ends.
- Virtual Plug & Charge grants nothing.

Nothing is deselected, neither at plug-in nor at unplug. A car that confirms a
few minutes after plug-in gets its rules then; without Virtual Plug & Charge
and with the charging release on, it charges without its plan until that
moment. A car that keeps reporting itself plugged in at home ten minutes after
the wallbox became empty has a stale plug signal; the log warns once.

A confirmed car must stay consistent while it charges. Its rules rest again,
with a warning in the log, when the car afterwards reports itself **unplugged**
while the wallbox still has a car two minutes later, or when the wallbox delivered **4 kWh** since charging started and the profile's
charge level reported since then has not risen. With BavarianData this is its
predicted charge level, which follows the car's charging between the car's rare
reports. The check skips a battery at 95 % or more, which may draw energy for
climate or balancing, and does nothing while there is no charge level. Only a
new plug-in report confirms the car again.
This catches a car that keeps reporting itself plugged in after it left.

The confirmation survives an HA restart. A car swapped while HA is not running
is not noticed. A car that only reports changes may not report again after a
quick unplug and replug; its rules then rest until its next report. Your car
charging at a second wallbox at home while another car is plugged in here
cannot be told apart from your car being plugged in here.

## Optional Virtual Plug & Charge

**Off by default.** Virtual Plug & Charge keeps charging disabled
until the active vehicle is recognized by its detection signals.
It uses your existing vehicle data; it does not send a vehicle-cloud request.
Automatic profile selection is a separate option: you can choose the profile
manually, but that choice alone never proves which car is connected.

1. Create a profile and configure its [cable and home signals](#optional-automatic-vehicle-selection).
   You can leave automatic profile selection off.
   Every profile with detection signals takes part.
2. Open the wallbox's device page and turn on **Virtual Plug & Charge** under
   **Configuration**. This switch controls the feature for the whole wallbox.
   The feature requires write access.
3. Connect the car and check the **Virtual Plug & Charge** status sensor. Allow time for
   the vehicle integration to report the new cable connection.

You can still switch the feature off on the wallbox
device page without a selected vehicle.

The wallbox must expose CP state and its local authorization setting (Modbus
register layout 01.02 or newer). For automatic charging without presenting an RFID card, local RFID
authorization must already be disabled in the wallbox's installation settings.
This feature controls the energy manager's charging release; it cannot switch
off or bypass the wallbox's RFID requirement.

Permission requires one unambiguous matching vehicle, that car's profile
selected, and a **new cable report for the current connection cycle**. The last confirmed
disconnected wallbox reading marks the earliest acceptable report; if the app
starts with a car already connected, it needs a report from after startup.
A matching value left over from a previous connection is insufficient. When BavarianData
provides the cable measurement's timestamp, that timestamp is used; otherwise
only the HA report time is available. The next normal vehicle update may be
needed before permission is granted.

The grant belongs to that observed connection. A manual pause
ends when a recognized vehicle is granted: plugging it in means it should charge.
The pause is lifted while the charging release is still off, so the release
follows immediately without the state-change interval. Only a
plug-in that HA observed after the car was unplugged counts: after an HA or app
restart, update or reconnect with the car still connected, the grant returns
without lifting the pause or replacing the percentage target.

With an armed [departure plan](departure-planning.md), manual or imported from
the car, the plan decides instead. The grant waits until the plan has applied its
decision: it keeps the car paused until the calculated start, or sets the planned
current. Charging therefore never starts briefly before a planned pause. A plan
that still lacks its target, charge level or capacity does not hold a manual
pause. A wallbox lock, a charging release switched off for this connection, reached target or a newer
queued command still prevents charging; the feature does not remove those
choices. While charging stays blocked, the status sensor's **Blocking reason**
attribute names the condition, such as **Departure plan is preparing the
current**. Once granted, a later missing or stale vehicle
report does not interrupt that connection. In service mode, its existing grant
also survives an HA Core restart because the app continues observing the wallbox.
A confirmed disconnect (control pilot A with an idle wallbox) or an app restart
requires a fresh grant. A briefly unreadable or contradictory
wallbox state is not treated as unplugging: the grant stays. Only when the
state remains unknown for 60 seconds is the release switched off; HA then
grants the same connection again as soon as the car is reported connected,
without replugging and without lifting a pause. Direct mode loses
its grant on a full HA restart or when its physical connection closes. Automatic profile changes preserve the open connection.
Choosing another profile alone also leaves an existing grant with that same
physical connection. Changing a vehicle's identity or detection sources invalidates the grant; the
next connection always needs a fresh match for the then-active profile.

Enabling the feature can stop an ongoing charge while it waits for a valid
grant. Disabling it **does not resume charging**: use **Charging release**
explicitly afterwards, and leave a wanted pause or lock in place. Check new
detection sources before relying on them.

Without Virtual Plug & Charge, an open charging release lets any car charge.
Turn on **Switch off charging release when unplugged** under
**Configuration** on the wallbox device page: after each confirmed disconnect,
Home Assistant switches the release off, and you switch it on for the next
charge. This runs in Home Assistant, so it is not enforced while HA is down.

Switching **Charging release** on while Virtual Plug & Charge
is enabled releases charging by hand, for any car, for the current connection.
Without a car plugged in, it releases the next plug-in. The Virtual Plug &
Charge status shows **Released by hand**. The next unplug ends this release and
Virtual Plug & Charge decides again; switching **Charging release** off ends it
earlier. This is how a guest charges without switching the feature off. The
guest car gets none of the selected car's rules, see
[the car's rules apply only to that car](#the-cars-rules-apply-only-to-that-car).
The release survives an HA or app restart.

Cable and home signals provide a convenience check, not cryptographic vehicle
identification or ISO 15118 Plug & Charge. Restored upstream values and a vehicle
change between wallbox polls cannot always be distinguished. For those limits,
see [charging permission](safety.md#virtual-plug--charge).

## Example: two cars, manual selection

Create **Family car** and **Second car**. Select Family car, configure its charge
level and optional limit, then repeat for Second car. Before charging, choose
the connected car in **Active vehicle** and set its intended target. Selecting
another car clears the previous percentage target and trip; it never starts
charging. If you do not want vehicle-based features for this charge, select
**No vehicle selected** and use the normal wallbox controls. With Virtual Plug & Charge
enabled, a new grant still needs a recognized active profile and matching
detection; choosing no vehicle does not bypass that requirement.

## Optional vehicle percentage target

The percentage target is **off by default**. Open the profile under
**Configure → Vehicles**, then **Charge level and charge limit → Configure charge
level**. Use the BavarianData profile's charge level or
select a **Vehicle charge level entity** from an existing vehicle integration.
This adds **Vehicle charge target** and its status sensor. The integration only
reads the selected vehicle data; it does not change the car's settings or estimate
battery capacity from wallbox energy.

Enable the separate option to use the car's maximum if you want to use the
profile's reported charge limit or select a **Vehicle charge limit entity**.
Only the chosen source's settings appear. Without a charge-limit source,
you can choose a target up to **100%**. With it, the reported car limit becomes
the maximum you can select. A falling car limit also lowers an existing target.
A target at the car's limit (the slider at its maximum) follows
every later change of that limit, up or down; a lower target never rises. A
target lowered to a falling limit is at the limit and follows it from then on.
A raised limit can end a reached target, but it never resumes the stop pause:
resume manually or plug in again. If this optional source is
temporarily unavailable, the last valid maximum is kept. Before its first valid
reading, setting a positive target is refused rather than assuming 100%.

For example, with a reported charge level of 45%, set a target of **80%**
below the car's own limit. Charging pauses when the vehicle reports at least
80%. A target at the car's own limit is only shown as reached:
the car ends that charge itself, like at any other charger, and nothing is
paused or switched off until you unplug. Setting a target never
starts charging or lifts a pause; **0%** switches this target off without
resuming. A reached target stays reached even if the reported charge level later
falls. Setting a new positive target establishes a new limit. With
[Virtual Plug & Charge](#optional-virtual-plug--charge), each recognized
connection starts with the car's current limit as its target, which also ends a
target reached during the previous connection. The independent
kWh target can also remain active: reaching either target stops the charge,
except that a percentage target at the car's own limit leaves stopping to the car.

As selected for this integration, missing or invalid vehicle data **does not
pause charging**. Monitoring resumes when a valid value returns. The same applies
while HA is down: the service can keep charging, but cannot evaluate an external
percentage until HA sends vehicle data again. Delayed or unchanged reports from
the vehicle integration can therefore allow the target to be exceeded. This is
not a replacement for the car's own charge limit. The five-second write floor
and wallbox response time also still apply when a stop is requested.

Changing or removing either selected source clears the old percentage target
and remembered maximum without starting charging. To cancel only the current
target while keeping its sources, set **Vehicle charge target** to **0%**.
Choose **Stop using vehicle charge level** under the profile's **Charge level
and charge limit** to remove that profile's charge-level and optional maximum assignments here, along with its
percentage target. The source entities in the vehicle integration are kept.
This also disables
departure planning and clears its trip, because that plan needs the SoC source.
The action is shown only when a vehicle charge-level source is configured. Vehicle profiles,
detection and Plug & Charge permission remain; the kWh goal and charging state
are unchanged.

## When data is missing

| What is missing? | What happens |
|---|---|
| Current vehicle charge level | Charging continues in its present state; the percentage target resumes monitoring when a valid report returns. Use the car's own limit for protection during data outages. |
| First valid optional vehicle maximum | A positive percentage target cannot be set yet. Disable that optional source if you want to choose your own maximum. |
| A previously valid vehicle maximum | The last valid maximum is retained; a later increase raises only a target at that maximum. |
| Usable battery capacity | The departure planner cannot calculate a new current; see [departure planning](departure-planning.md). |
| Detection signals are unclear | Automatic mode keeps the previously selected car until another car or an unknown car at the wallbox is detected. Choose the correct profile manually if needed. |
| Virtual Plug & Charge is waiting for detection | Wait for a new cable report for this connection and check that the recognized car's profile is selected. Manually selecting it does not replace the detection check. |
| Virtual Plug & Charge shows **Charging blocked** | Open the status sensor and check its **Blocking reason** attribute, for example a switched-off charging release, a wallbox lock or authorization, a reached target or a missing register value. |
| Charging history cannot be matched | Loss estimates stay unknown instead of being assigned to the wrong charge. See [charging estimates](charging-estimates.md). |

For absent BavarianData entities, open the vehicle's HA entity list and enable
only the sources you need. This integration does not activate vehicle entities
or request a new vehicle-cloud update for you.

[Next departure](departure-planning.md) · [Losses and costs](charging-estimates.md) · [Everyday controls](user-guide.md)
