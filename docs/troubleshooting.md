# Troubleshooting

Work down this page in order: each section assumes the ones above it are fine.

## The config flow cannot connect

The wallbox is not answering on the port, address and bus parameters you
entered.

**Let the integration look for it.** The setup form's last field offers a
search. *Search every device address* tries all documented addresses at your
bus parameters and takes about twenty seconds. *Search every address, baud
rate and frame* tries every documented combination and takes several minutes.
Both only read, so neither can disturb a wallbox.

If the search finds nothing, the problem is below the protocol:

- **Wiring.** Modbus `A` is `+`, `B` is `−`, and `GND` has to be connected.
  Swapped A/B is the single most common cause and looks exactly like a dead
  bus.
- **Satellite mode.** DIP switches 4 and 5 on bank S1 have to be on, and the
  wallbox has to be restarted afterwards. Without it the wallbox answers
  nothing.
- **The port.** Check that the adapter is really where you think it is:
  `docker exec homeassistant ls -l /dev/serial/by-id/`. Use the `by-id` path.
- **Permissions.** Home Assistant has to be able to open the device. On a
  Container install the device has to be passed into the container.

The shipped bus check isolates all of this from Home Assistant; see
[quickstart.md](quickstart.md#5-prove-the-bus-before-writing-anything).

## The device answered but reported no layout version

Something is on the bus and talks Modbus, but it is not an AMTRON, or it is
not in satellite mode. Check DIP 4 and DIP 5 on bank S1 again, and that no
other device is using the same address.

## Writes are refused

The same cause: a wallbox that is not in satellite mode answers reads and
refuses writes. The integration raises a repair issue for this.

If writes were working and stopped, check whether something else started
driving the bus. RS-485 has exactly one master.

## Error 200, "Energy manager unavailable"

The wallbox did not get a heartbeat in time. Press *Fix* on the repair issue,
or press the **Recover from error** button; both write the documented
sequence of heartbeat, charging release and charging current.

If it comes back, the serial link is dropping frames:

- check the cabling, the ground connection and the bus termination,
- shorten the cable or lower the baud rate with the MENNEKES configuration
  tool,
- make sure nothing else transmits on the bus.

Configure an energy-manager fallback with the configuration tool so a Home
Assistant restart has a defined outcome. The integration raises a repair issue
when none is configured.

## Entities are missing

The firmware's register layout is older than v01.03, or the hardware cannot
switch phases. The integration creates no entity for a register the device
does not have, rather than an entity stuck at `unknown`.

Enable the **Modbus layout version** sensor (diagnostic, disabled by default)
to see which layout you have, and compare against the **Needs** column in the
[entity reference](entities.md). A repair issue appears for anything older
than v01.03.

## No control entities at all

The entry is in read-only mode, which is the default. Open the entry options
and switch the control mode to *Modbus master*. The entry reloads and the
number, switches, selects and buttons appear.

## The charging current does not follow

- **Within five seconds of the last change:** the manufacturer allows one
  change every five seconds. The integration remembers the newest value and
  writes it when the interval has passed, so the last value you set wins.
- **The wallbox clamps it.** The signalled current is the smallest of your
  limit, the downgrade current, the maximum EVSE current and the maximum house
  current. Check those four diagnostic sensors.
- **A downgrade is active.** `binary_sensor.*_downgrade_active` on means the
  grid operator is limiting the wallbox in hardware. Nothing on the Modbus
  side can raise it.

## The slider jumps back

While charging is paused the register holds the pause value, which is below
the entity's minimum, so the entity shows the stored setpoint instead. Turn
off the **Charging paused** switch to resume.

## Phase switching does nothing

- The hardware has to support it: `binary_sensor.*_phase_switch_capable` has
  to be on. Only 11 kW devices do.
- The manufacturer asks for more than five minutes between switches. The
  integration refuses a faster change and says how long to wait.
- Some vehicles do not tolerate phase switching at all.

## Getting help

Open an issue with the diagnostics download attached. It contains the full raw
register image, which is usually what decides the question, and it contains
neither the serial port path nor the wallbox serial number.
