# Modbus Register Map

This is the project's own description of the register map the integration
implements. It is **not** a copy of the manufacturer's document.

Source: MENNEKES, "Modbus RTU Specification -- AMTRON 4You 300 / Compact 2.0s /
Start 2.0s", document revision **2.5**, dated **2025-07-11**, internal register
layout **v01.03**. Obtain the document from the MENNEKES website; this
repository does not vendor it. Register addresses, data types and enumerated
values are facts restated here so the code can be reviewed; the
specification's explanatory prose is not reproduced.

`custom_components/mennekes_amtron/registers.py` is the machine-readable
version of this table and the single source of truth in the code.
`scripts/check_register_map.py` fails the build when the two drift apart: it
compares every address, span, data type, access mode, layout version, unit,
read block and enumerated value below against the code, and separately
against the entities and translations built on top of them.

## Bus parameters

| Setting | Factory default |
|---|---|
| Physical layer | RS-485, Modbus `A` = `+`, `B` = `-` |
| Baud rate | 57600 |
| Frame | 8 data bits, 2 stop bits, no parity |
| Byte and word order | big endian |
| Device address | 50, settable from 10 to 50 |
| Read function codes | 0x03 (holding) and 0x04 (input), equivalent |
| Write function codes | 0x06 single register, 0x10 multiple registers |

A 32-bit value therefore occupies two registers and has to be written with
function 0x10.

## Registers

| Address | Type | Access | Layout | Key | Unit |
|---|---|---|---|---|---|
| `0x0000` | uint16 | R | v01.00 | `modbus_layout_version` | - |
| `0x0001`-`0x0008` | ascii | R | v01.00 | `firmware_version` | - |
| `0x0013`-`0x001A` | ascii | R | v01.02 | `serial_number` | - |
| `0x001B`-`0x0022` | ascii | R | v01.03 | `article_number` | - |
| `0x0100` | uint16 | R | v01.00 | `evse_state` | - |
| `0x0101` | uint16 | R | v01.00 | `authorization_status` | - |
| `0x0102` | uint16 | R | v01.00 | `downgrade_status` | - |
| `0x0103` | uint16 | R | v01.00 | `phase_rotation` | - |
| `0x0108` | uint16 | R | v01.02 | `cp_state` | - |
| `0x0114`-`0x0115` | float32 | R | v01.03 | `signaled_current` | A |
| `0x0300`-`0x0301` | float32 | R | v01.00 | `downgrade_current` | A |
| `0x0302`-`0x0303` | float32 | R/W | v01.00 | `charging_current_ems` | A |
| `0x0304`-`0x0305` | float32 | R | v01.00 | `max_current_house` | A |
| `0x0306`-`0x0307` | float32 | R | v01.00 | `max_evse_current` | A |
| `0x030A` | uint16 | R | v01.00 | `phase_switching_mode` | - |
| `0x030C` | uint16 | R | v01.01 | `phase_options_hw` | - |
| `0x030D` | uint16 | R | v01.02 | `cable_lock_setting` | - |
| `0x030E` | uint16 | R | v01.02 | `ems_fallback_current` | A |
| `0x030F` | uint16 | R | v01.02 | `grid_imbalance` | - |
| `0x0310` | uint16 | R | v01.02 | `grid_imbalance_threshold` | A |
| `0x0311` | uint16 | R | v01.02 | `grid_phases_connected` | - |
| `0x0312` | uint16 | R | v01.02 | `authorization_enabled` | - |
| `0x0313` | uint16 | R | v01.02 | `solar_min_current` | A |
| `0x0314` | uint16 | R | v01.02 | `phase_switching_pause` | s |
| `0x0500`-`0x0501` | float32 | R | v01.00 | `current_l1` | A |
| `0x0502`-`0x0503` | float32 | R | v01.00 | `current_l2` | A |
| `0x0504`-`0x0505` | float32 | R | v01.00 | `current_l3` | A |
| `0x0506`-`0x0507` | float32 | R | v01.00 | `voltage_l1` | V |
| `0x0508`-`0x0509` | float32 | R | v01.00 | `voltage_l2` | V |
| `0x050A`-`0x050B` | float32 | R | v01.00 | `voltage_l3` | V |
| `0x050C`-`0x050D` | float32 | R | v01.00 | `power_l1` | W |
| `0x050E`-`0x050F` | float32 | R | v01.00 | `power_l2` | W |
| `0x0510`-`0x0511` | float32 | R | v01.00 | `power_l3` | W |
| `0x0512`-`0x0513` | float32 | R | v01.00 | `power_total` | W |
| `0x0900`-`0x0901` | float32 | R | v01.02 | `temperature` | degC |
| `0x0B00`-`0x0B01` | float32 | R | v01.00 | `session_max_current` | A |
| `0x0B02`-`0x0B03` | float32 | R | v01.00 | `session_energy` | kWh |
| `0x0B04`-`0x0B05` | uint32 | R | v01.00 | `session_duration` | s |
| `0x0B06` | uint16 | R | v01.02 | `detected_ev_phases` | - |
| `0x0D00` | uint16 | W | v01.00 | `heartbeat` | - |
| `0x0D02` | uint16 | R | v01.00 | `cable_lock_status` | - |
| `0x0D03` | uint16 | R/W | v01.00 | `solar_charging_mode` | - |
| `0x0D04` | uint16 | R/W | v01.00 | `requested_phases` | - |
| `0x0D05` | uint16 | R/W | v01.00 | `charging_release` | - |
| `0x0D06` | uint16 | R/W | v01.00 | `lock_evse` | - |
| `0x0D19` | uint16 | W | v01.03 | `system_restart` | - |
| `0x0E00` | uint16 | R | v01.00 | `error_code` | - |
| `0x0E01` | uint16 | R | v01.02 | `master_lost_fallback` | - |
| `0x0E02` | uint16 | R | v01.02 | `switched_phases` | - |
| `0x1000`-`0x1001` | float32 | R | v01.02 | `energy_total` | kWh |
| `0x1002`-`0x1003` | uint32 | R | v01.02 | `sessions_total` | - |

## Enumerated values

The integration turns these into Home Assistant state options; the option name
is the lowercased member name of the matching Python enum in `enums.py`.

| Register | Value | Meaning |
|---|---|---|
| `evse_state` | `0` | Not initialized |
|  | `1` | Idle, no vehicle connected |
|  | `2` | Vehicle connected, no charging current signalled |
|  | `3` | Preconditions valid, not charging yet |
|  | `4` | Ready to charge, charging current signalled |
|  | `5` | Charging |
|  | `6` | Error |
|  | `7` | Service mode |
| `authorization_status` | `0` | Not used |
|  | `1` | Authorized |
|  | `2` | Not authorized |
| `downgrade_status` | `0` | Not relevant, no vehicle connected |
|  | `1` | Charging current not downgraded |
|  | `2` | Charging current downgraded |
| `phase_rotation` | `0` | L1-L2-L3 |
|  | `1` | L2-L3-L1 |
|  | `2` | L3-L1-L2 |
| `cp_state` | `0` | Init |
|  | `10` | A1, no vehicle |
|  | `11` | B1, vehicle connected, not charging |
|  | `12` | C1, vehicle ready to charge |
|  | `13` | D1 |
|  | `14` | E, error |
|  | `15` | F, error |
|  | `26` | A2, vehicle disconnected, current offered |
|  | `27` | B2, ready to charge, current offered |
|  | `28` | C2, charging, current offered |
|  | `29` | D2 |
| `phase_switching_mode` | `0` | Solar, one phase only |
|  | `1` | Solar, three phases only |
|  | `2` | Solar, dynamic one or three phases |
| `phase_options_hw` | `0` | Hardware supports one phase only |
|  | `1` | Hardware supports three phases only |
|  | `2` | Hardware supports one or three phases, switching possible |
| `ems_fallback_current` | `0` | Fallback handling disabled, charging continues with the last values |
|  | `1` | Fallback handling enabled, charging pauses on heartbeat timeout |
|  | `6-32` | Fallback current in ampere |
| `grid_phases_connected` | `0` | L1 connected |
|  | `2` | L1, L2 and L3 connected |
| `detected_ev_phases` | `0` | Not initialized |
|  | `1` | One phase |
|  | `2` | Two phases |
|  | `3` | Three phases |
| `cable_lock_status` | `0` | Locking state undetermined |
|  | `1` | Cable unlocked |
|  | `2` | Cable locked |
|  | `3` | Wallbox with a fixed cable |
| `solar_charging_mode` | `0` | Solar charging not active |
|  | `1` | Fast charging, standard |
|  | `2` | Solar charging, Sunshine |
|  | `3` | Solar supported charging, Sunshine+ |
| `requested_phases` | `0` | Charge on all available phases |
|  | `1` | Force single-phase charging |
| `switched_phases` | `0` | All available phases will be used |
|  | `1` | One phase will be used |
| `error_code` | `0` | No error |
|  | `200` | Energy manager unavailable, no heartbeat |
|  | `2011` | Vehicle drew more current than was signalled |
|  | `2300-2305` | Over- or undervoltage on L1, L2 or L3 |
|  | `2323` | Connected phases mismatch |

## Read blocks

The coordinator reads contiguous ranges, not single registers. A block is only
read when the device's layout version supports every register in it; a device
that still rejects the range read falls back to single-register reads for that
block.

| Block | Address | Registers | Minimum layout |
|---|---|---|---|
| `identity` | `0x0000` | 9 | v01.00 |
| `serial_number` | `0x0013` | 8 | v01.02 |
| `article_number` | `0x001B` | 8 | v01.03 |
| `status` | `0x0100` | 4 | v01.00 |
| `cp_state` | `0x0108` | 1 | v01.02 |
| `signaled_current` | `0x0114` | 2 | v01.03 |
| `current_limits` | `0x0300` | 8 | v01.00 |
| `phase_switching_mode` | `0x030A` | 1 | v01.00 |
| `phase_options_hw` | `0x030C` | 1 | v01.01 |
| `configuration` | `0x030D` | 8 | v01.02 |
| `measurements` | `0x0500` | 20 | v01.00 |
| `temperature` | `0x0900` | 2 | v01.02 |
| `session` | `0x0B00` | 6 | v01.00 |
| `detected_ev_phases` | `0x0B06` | 1 | v01.02 |
| `functions` | `0x0D02` | 5 | v01.00 |
| `error_code` | `0x0E00` | 1 | v01.00 |
| `fallback_state` | `0x0E01` | 2 | v01.02 |
| `statistics` | `0x1000` | 4 | v01.02 |

## Rules the integration implements

- **`0x0302` is not a plain limit.** `0` means *no limitation* and makes the
  wallbox signal its maximum current. `0.01`-`5.99` is invalid and makes it
  signal 0 A, which is the documented way to pause. `>= 6` is a real limit.
- **A charging session needs three things**: a heartbeat at `0x0D00` at least
  every ten seconds, the charging release at `0x0D05 = 1`, and a charging
  current of at least 6 A at `0x0302`.
- **Recovery** from the error state after a reboot or a lost heartbeat means
  writing `0x0D00`, `0x0D05` and `0x0302`, in that order.
- **Values are clamped by the wallbox**, to the smallest of the energy-manager
  current, the downgrade current, the maximum EVSE current and the maximum
  house-connection current.
- **Phase switching** needs `0x030C = 2`. Some vehicles need the charging
  current set to the pause value before `0x0D04` changes. The wallbox performs
  the switch itself, including the pause configured in `0x0314`.
- **`0x0D19`** restarts the wallbox with the value `0xBB` and may only be used
  while the wallbox is idle.
- **`0x1000`** is marked by the manufacturer as not usable for billing.
- **Timing**: change the charging current no faster than every five seconds;
  keep pauses, resumes and phase switches more than five minutes apart and use
  hysteresis.
