# Set up your vehicle data

[Documentation](README.md) › Vehicle setup

Basic wallbox controls do not need a vehicle integration. Add vehicle data only
for a percentage target, a departure plan, charging-loss estimates or optional
automatic charging permission. Existing Home Assistant entities work with any
suitable vehicle integration; BavarianData
can also supply supported data from one selected BMW vehicle.

**Availability:** manual vehicle entities and percentage targets are available in
stable 0.6.1. Central vehicle profiles, optional automatic selection and
Virtual Plug & Charge are new in **beta 0.8.0b1**. The new menu layout is shown
below; existing manual sources continue to work after updating.
See [versions and feature availability](README.md#versions-and-feature-availability).

## Choose the setup that fits

| Your setup | What to do |
|---|---|
| No vehicle integration | Use the current slider, pause switch and optional kWh target. No vehicle setup is needed. |
| One car with an existing SoC sensor | Under **Configure → Vehicle**, choose that charge-level entity. Add a maximum-limit entity only if you want one. |
| One BavarianData car | Create one profile, select its vehicle once, then enable only the data you want to use. |
| Several cars | Create a profile for each car and select the connected car before charging. Each profile keeps its own optional settings and loss average. |
| Choose the car automatically | First configure working profiles, then opt into cable/home detection. Manual selection always remains available. |
| Allow a recognized car to charge automatically | Configure its detection signals, permit that profile and separately enable [Virtual Plug & Charge](#optional-virtual-plug--charge). |

A profile does not enable every feature at once:

| Optional choice | What it enables |
|---|---|
| Charge level | A percentage charging target |
| Vehicle charge limit | A maximum selectable percentage based on the car's own limit |
| Usable battery capacity | Planning current for a selected departure time |
| Vehicle departure timers | A button to copy the next supported vehicle climate timer |
| Vehicle charging history | Better matching for estimated charging losses |
| Cable and home signals | Optional selection of the active profile |
| Virtual Plug & Charge permission | Optional charging permission for a recognized, permitted profile |

## Vehicle profiles

Vehicle profiles keep the data and settings of different cars together. You can
configure up to eight profiles. Manual selection is the default; optional
[automatic selection](#optional-automatic-vehicle-selection) can use cable and
home signals. Adding a profile does not enable a percentage target, departure
planning or loss estimates.
Enable only the features you want for that profile.

Open **Configure → Vehicle → Vehicle profiles → Add profile**. Give the profile
a name, choose **BavarianData**, then choose the vehicle device. Select the car
using **Active vehicle** on the wallbox device, or **Select vehicle** in the
profile menu. The integration finds that vehicle's supported data through Home
Assistant's entity registry, so renamed entities do not require guessed entity
names. Each optional feature uses the same selected car. No BMW credentials or
additional cloud requests are needed.

The charge level, the car's maximum charge limit, usable battery capacity,
departure timers and charging history are separate choices. A missing value
does not cause the integration to select another car. Unsupported or unavailable
data stays unavailable; manual sources remain an alternative.

Enable the corresponding vehicle entities in BavarianData if they are disabled.
The adapter reads the reported high-voltage battery charge level, target charge
level and maximum usable battery energy. It does not enable entities itself.
For capacity, it uses the car's reported usable energy when full, accounting for
its current battery condition; it does not substitute nominal battery size or a
battery-health percentage. If that value is missing, choose a suitable manual
capacity source instead.

Check the selected profile **before charging**, whether you chose it manually or
enabled automatic selection. Changing the active profile clears the previous
car's percentage target and departure plan without starting charging. Charging-loss averages are kept separately for
each profile; changing its data source starts a new average for that source.
A charge that began before you selected a profile is not newly assigned to it.
The profile's saved last result and average remain available when you return
to it.

Existing manually selected sources keep working after an update. They are not
silently replaced by a BavarianData vehicle, even when old departure and history
settings refer to different cars. When you start using profiles, those existing
settings are retained as a manual profile.

### Optional automatic vehicle selection

Automatic selection is **off by default** and separately configurable for each
profile. Open **Configure → Vehicle → Automatic vehicle selection**. Under
**Configure a profile’s detection signals**, select a profile and choose:

- **Use the vehicle integration**: BavarianData supplies the vehicle's cable
  sensor and GPS tracker. Its position is compared with Home Assistant's Home
  zone. Existing enabled entities are used; no extra cloud requests or vehicle
  commands are sent.
- **Select Home Assistant entities**: choose two separate signals for this car.
  The cable source must be a binary sensor with `on` meaning connected. The home
  source may be a binary sensor with `on` meaning at home, or a `person`/
  `device_tracker` with state `home`. Prefer a signal that follows the vehicle;
  a person's presence alone does not establish where the car is.
- **Detection disabled for this profile**: exclude this profile from automatic
  selection while keeping its manual selection and optional features.

After configuring the participating profiles, enable **Select the vehicle
automatically**. The **Active vehicle** selector also offers **Automatic
selection** when detection sources are configured. Open the selector’s details
to see the currently selected profile. Selecting a particular car or no vehicle
manually turns the automatic mode off.

The integration selects a car only when exactly one configured profile reports
both **cable connected** and **at home**. A positive change must remain stable
for three seconds. A valid disconnected or away signal excludes that car, even
if its other signal is missing. For the remaining candidates, unknown or
unavailable signals block selection, as do signals not reported to Home Assistant
within 30 minutes.
No matching car or several matching cars means **no active vehicle**. Previous
car-specific targets and the departure plan are cleared when the assignment
changes; detection never starts charging or lifts a pause. If the result is
unclear, choose the correct profile manually.

These signals are an optional selection aid, not an identity supplied by the
wallbox. The 30-minute check concerns the latest **Home Assistant report**, which
cannot always establish when the vehicle originally measured its cable state
or position. Other integrations can report saved values again after restarting.
BavarianData can restore both the cable state and tracker coordinates; an exposed
BMW cable timestamp is checked, but missing timestamps cannot prove measurement
freshness. The GPS accuracy area must also fit fully inside or outside the Home
zone; a position crossing its boundary remains unknown. Check the selected car
before relying on its charge target or loss estimate.

Charging-loss assignment remains conservative: automatic recognition after a
charge has already begun does not retrospectively assign that charge to the
new profile. Saved loss results and averages remain separate for each car.

## Optional Virtual Plug & Charge

**New in beta 0.8.0b1 and off by default.** Virtual Plug & Charge keeps charging disabled
until the active vehicle is recognized and its profile is permitted.
It uses your existing vehicle data; it does not send a vehicle-cloud request.
Automatic profile selection is a separate option: you can choose the profile
manually, but that choice alone never proves which car is connected.

1. Create a profile and configure its [cable and home signals](#optional-automatic-vehicle-selection).
   You can leave automatic profile selection off.
2. Select that profile. Open **Configure → Vehicle → Virtual Plug & Charge**
   and enable **Allow this vehicle to charge automatically**. Repeat for any
   other profiles you want to permit.
3. Turn on **Enable Virtual Plug & Charge** in the same section. The feature requires
   write access. In service mode, update the app to **0.8.0b1** first: older
   compatible services do not provide this charging-permission capability.
4. Connect the car and check the **Virtual Plug & Charge** status sensor. Allow time for
   the vehicle integration to report the new cable connection.

The wallbox must expose CP state and its local authorization setting (Modbus
register layout 01.02 or newer). For automatic charging without presenting an RFID card, local RFID
authorization must already be disabled in the wallbox's installation settings.
This feature controls the energy manager's charging release; it cannot switch
off or bypass the wallbox's RFID requirement.

Permission requires one unambiguous matching vehicle, an allowed active profile
and a **new cable report for the current connection cycle**. The last confirmed
disconnected wallbox reading marks the earliest acceptable report; if the app
starts with a car already connected, it needs a report from after startup.
A matching value left over from a previous connection is insufficient. When BavarianData
provides the cable measurement's timestamp, that timestamp is used; otherwise
only the HA report time is available. The next normal vehicle update may be
needed before permission is granted.

The grant belongs to that observed connection. A deliberate pause, wallbox lock,
disabled charging release or reached target still prevents charging; the feature
does not remove those choices. Once granted, a later missing or stale vehicle
report does not interrupt that connection. In service mode, its existing grant
also survives an HA Core restart because the app continues observing the wallbox.
A disconnect, unknown wallbox connection state or app restart requires a fresh
grant. Direct mode loses its grant on a full HA restart or when its physical
connection closes. Automatic profile changes preserve the open connection.
Choosing another profile alone also leaves an existing grant with that same
physical connection. Removing a vehicle's permission or changing its identity
or detection sources invalidates the grant; the next connection always needs a
fresh match for the then-active permitted profile.

Enabling the feature can stop an ongoing charge while it waits for a valid
grant. Disabling it **does not resume charging**: use **Charging release**
explicitly afterwards, and leave a wanted pause or lock in place. Changing a
profile's vehicle or detection sources clears its saved automatic permission;
check the new sources before allowing it again.

If you previously switched **Charging release** off, switch it on when you want
to allow charging again. With virtual Plug & Charge enabled this records your
request; it still waits for the permitted vehicle and cannot bypass recognition.

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
enabled, a new grant still needs a permitted active profile and matching
detection; choosing no vehicle does not bypass that requirement.

## Optional vehicle percentage target

The percentage target is **off by default**. Open **Configure → Vehicle →
Configure charge level**. Use the active BavarianData profile's charge level or
select a **Vehicle charge level entity** from an existing vehicle integration.
This adds **Vehicle charge target** and its status sensor. The integration only
reads the selected vehicle data; it does not change the car's settings or estimate
battery capacity from wallbox energy.

Enable the separate option to use the car's maximum if you want to use the
profile's reported charge limit or select a **Vehicle charge limit entity**.
Only the chosen source's settings appear. Without a charge-limit source,
you can choose a target up to **100%**. With it, the reported car limit becomes
the maximum you can select. A falling car limit also lowers an existing target;
a later increase never raises the target you chose. If this optional source is
temporarily unavailable, the last valid maximum is kept. Before its first valid
reading, setting a positive target is refused rather than assuming 100%.

For example, with a reported charge level of 45%, set a target of **80%**.
Charging pauses when the vehicle reports at least 80%. Setting a target never
starts charging or lifts a pause; **0%** switches this target off without
resuming. A reached target stays reached even if the reported charge level later
falls. Setting a new positive target establishes a new limit. The independent
kWh target can also remain active: reaching either target stops the charge.

As selected for this integration, missing or invalid vehicle data **does not
pause charging**. Monitoring resumes when a valid value returns. The same applies
while HA is down: the service can keep charging, but cannot evaluate an external
percentage until HA sends vehicle data again. Delayed or unchanged reports from
the vehicle integration can therefore allow the target to be exceeded. This is
not a replacement for the car's own charge limit. The five-second write floor
and wallbox response time also still apply when a stop is requested.

Changing or removing either selected source clears the old percentage target
and remembered maximum without starting charging. Choose **Disable percentage
target** under **Vehicle** to remove this optional feature. This also disables
departure planning and clears its trip, because that plan needs the SoC source.
The kWh goal is independent. In service mode, changing a percentage target
requires an app version that supports confirmed target updates. Update the
Modbus service app if Home Assistant asks you to do so. Other service functions
remain available with older compatible app versions.

## When data is missing

| What is missing? | What happens |
|---|---|
| Current vehicle charge level | Charging continues in its present state; the percentage target resumes monitoring when a valid report returns. Use the car's own limit for protection during data outages. |
| First valid optional vehicle maximum | A positive percentage target cannot be set yet. Disable that optional source if you want to choose your own maximum. |
| A previously valid vehicle maximum | The last valid maximum is retained; a later increase does not raise the target you already chose. |
| Usable battery capacity | The departure planner cannot calculate a new current; see [departure planning](departure-planning.md). |
| Detection signals are unclear | Automatic mode selects no vehicle. Choose the correct profile manually if needed. |
| Virtual Plug & Charge is waiting for detection | Wait for a new cable report for this connection and check that the selected profile is permitted. Manually selecting it does not replace the detection check. |
| Charging history cannot be matched | Loss estimates stay unknown instead of being assigned to the wrong charge. See [charging estimates](charging-estimates.md). |

For absent BavarianData entities, open the vehicle's HA entity list and enable
only the sources you need. This integration does not activate vehicle entities
or request a new vehicle-cloud update for you.

[Next departure](departure-planning.md) · [Losses and costs](charging-estimates.md) · [Everyday controls](user-guide.md)
