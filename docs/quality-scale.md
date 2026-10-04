# Home Assistant Quality Scale

Target: Platinum.

Current status: Not Platinum yet. The integration implements every Platinum
rule in code, but two rules are tracked as open and are listed below.

`custom_components/mennekes_amtron/quality_scale.yaml` is the machine-readable
status of all 52 official rules.
`scripts/check_quality_scale.py` fails the build when it drifts from this
document.

## Platinum Blockers

- `brands`: the integration needs assets in the `home-assistant/brands`
  repository before a core submission. The repository ships project-owned
  generic artwork under `custom_components/mennekes_amtron/brand/` in the
  meantime, pinned by hash in `docs/legal.md`.
- `test-coverage`: the local ratchet is 95 percent. The rule stays open until
  the full suite consistently exceeds the Platinum target rather than just
  meeting the ratchet.

## Repository status

The repository is private while the integration waits for its on-device
verification. Two consequences are visible in CI: the CodeQL workflow only
runs once the repository is public, because code-scanning uploads need GitHub
Advanced Security on a private repository, and the `brands` blocker cannot be
closed before a public submission either.

## Rules that are exempt, and why

- `entity-event-setup`: all entities derive from the coordinator. There is no
  device-side event stream to subscribe to in an entity lifecycle callback.
- `integration-owner`: the repository deliberately ships no personal metadata,
  so `manifest.json` keeps an empty `codeowners` list. The maintenance scope
  is documented in `SUPPORT.md` instead.
- `reauthentication-flow`: a serial bus has no credentials to re-enter.
- `discovery` and `discovery-update-info`: a USB RS-485 adapter cannot be
  identified as this wallbox. The config flow offers a port list, which is
  honest, instead of claiming a discovery it cannot perform.
- `dynamic-devices` and `stale-devices`: one config entry models exactly one
  wallbox.
- `inject-websession`: the integration speaks serial Modbus RTU and makes no
  HTTP request.

## Rules this integration earns that a push integration cannot

- `appropriate-polling`: the device has no push channel, so polling is the
  correct model. The interval is five seconds by default and configurable, the
  reads are blocked into eighteen contiguous ranges rather than fifty single
  reads, and the heartbeat is deliberately kept out of the poll.
- `test-before-configure`: the config flow opens the serial port and reads the
  layout version and serial number before an entry is created.
- `log-when-unavailable`: the serial connection is held open, so the
  coordinator logs a loss and a recovery exactly once per state change.
