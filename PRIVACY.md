# Privacy

## What this integration collects

Nothing leaves the local network. The integration speaks Modbus RTU over a
serial port and makes no outbound network request at all.

## What is stored in Home Assistant

- The serial port path, bus parameters and Modbus device address, in the
  config entry.
- The wallbox serial number, if the firmware exposes it, as the config entry's
  unique id and in the device registry.
- Register values, as entity states and in Home Assistant's recorder database
  like any other entity.

## What diagnostics contain

The diagnostics download is built from an allowlist. It deliberately omits:

- the serial port path — a `/dev/serial/by-id/...` path contains the USB
  adapter's serial number. Only a category (`by-id`, `tty`, `other`) is
  reported.
- the wallbox serial number — only a non-reversible FNV-1a 64 hash of it.

It includes the bus parameters, the device capabilities, the write counters
and the full raw register image, because that is what makes a support report
decidable.

## Charging data

Charging sessions say when somebody was at home and when a vehicle was
charged. That data stays in your Home Assistant instance; if you export or
share it, you are the one deciding to share it.
