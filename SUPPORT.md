# Support Policy

## Supported Release Line

- `0.1.x`: active

## Compatibility Baseline

- Minimum Home Assistant: `2026.5.0`
- Validated Home Assistant: `2026.5.x` and `2026.9.x`
- Python: `3.14`
- Modbus register layout: `v01.03` (validated), `v01.00`–`v01.02` supported
  with capability gating and untested
- pymodbus: `3.13.1`, matching the pin of Home Assistant Core's own `modbus`
  integration

Home Assistant Core currently ships `pymodbus`, `tmodbus` and
`modbus-connection` side by side for its built-in `modbus` integration, so
Core may be migrating away from pymodbus. That does not change anything for
this integration today: the pin is explicit and the API is verified against
`3.13.1`. If Core drops pymodbus, Home Assistant will simply install it for
this integration alone, and `scripts/check_pymodbus_pin.py` then checks an
upper bound instead of equality with Core.

## Validated Hardware

- AMTRON 4You 310 11 C2, article `1313201205`, 11 kW, fixed cable, 16 A.

Other AMTRON models covered by the same Modbus specification (4You 300,
Compact 2.0s, Start 2.0s) are expected to work through capability gating but
are **untested**. Issues from those models are welcome and will be labelled as
unvalidated hardware.

## Maintenance Scope

Security and compatibility fixes target the current minor release line only,
unless a release note states otherwise.
