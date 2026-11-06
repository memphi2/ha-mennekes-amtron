# Home Assistant quality scale

Target: Platinum.

Current status: Platinum **(local self-assessment)**. This community integration
has not been awarded a tier by Home Assistant. The README badge links here so
its scope, evidence and exceptions remain visible.

The assessment follows the [official quality-scale rules](https://contributors.home-assistant.io/core/quality-scale/)
reviewed on **2026-11-05**: 20 Bronze, 10 Silver, 21 Gold and 3 Platinum rules.
Open rules: none in this local assessment. Distribution-specific adaptations
for this custom integration are explained below; they do not establish approval
for inclusion in Home Assistant Core.

| Status | Count |
|---|---|
| done | 50 |
| exempt | 4 |
| todo | 0 |
| total | 54 |

The [quality-scale file](../custom_components/mennekes_amtron_rtu/quality_scale.yaml)
records each rule and its rationale. [The quality gate](../scripts/check_quality_scale.py)
checks the rule inventory, counts, exemptions and this document. An open rule
must be recorded as a blocker; a missing rule cannot silently pass.

## Evidence by tier

| Tier | What is implemented | Evidence |
|---|---|---|
| Bronze | UI setup validates wallbox identity before creating an entry. USB, transparent RTU-over-TCP, translating Modbus TCP gateways and the independent service have explicit connection paths. Unique IDs prevent duplicate wallboxes; actions are registered once. | [Config flow](../custom_components/mennekes_amtron_rtu/config_flow.py), [flow tests](../tests/test_config_flow.py), [service flow tests](../tests/test_service_flow.py), [installation](installation.md) |
| Silver | Failed setup retries, disconnected entities become unavailable, unload closes the resources actually loaded, and authentication failures start an access-renewal flow. The manifest names the public maintainer `@memphi2`; the test gate requires 99% combined statement/branch coverage. | [Lifecycle tests](../tests/test_platform_lifecycle.py), [reauthentication tests](../tests/test_service_reauth.py), [coverage gate](../scripts/check_coverage.py), [support policy](../SUPPORT.md) |
| Gold | Supervisor discovers the service app and refreshes an existing entry without replacing its devices or entities. Names, enumerated states and user-facing errors are translated into English, German, French, Dutch and Italian. Diagnostics, repairs, connection changes and user guides cover supported functions and limitations. | [Discovery tests](../tests/test_service_discovery.py), [translations](../custom_components/mennekes_amtron_rtu/translations), [register/translation gate](../scripts/check_register_map.py), [user guide](user-guide.md), [troubleshooting](troubleshooting.md) |
| Platinum | Modbus and service HTTP requests use asynchronous clients. HA supplies its shared web session to the service client. Strict typing covers integration and tooling code against the current supported HA annotations. | [Device client](../custom_components/mennekes_amtron_rtu/device/client.py), [remote client](../custom_components/mennekes_amtron_rtu/remote.py), [typing gate](../scripts/check_typing.py), [validation guide](validation.md) |

The heartbeat and command-result event entities subscribe when added to HA and
unregister their listeners when removed. It therefore satisfies `entity-event-setup`; using a coordinator
does not make that rule inapplicable. Service entries also make `discovery`,
`discovery-update-info`, `reauthentication-flow` and `inject-websession`
applicable, even though direct Modbus connections have no credentials.

For an authentication failure, HA retrieves the existing app's current
credentials from Supervisor automatically. If that fails, HA OS users confirm
the access prompt after checking the app. Standalone users enter their current
service token. Entries bound to a serial number require the same serial number;
standalone entries originally added without one retain their URL-based identity.
That fallback does not independently verify the physical wallbox. Both paths
keep the existing entry. See [Renew service access](troubleshooting.md#renew-service-access).

## Custom-integration scope

The official scale is a Core review process. Its documentation rules refer to
pages on home-assistant.io; this custom integration publishes the corresponding
installation, usage, action and troubleshooting guides in this repository.
The local assessment evaluates those guides. It does not claim an official
Home Assistant integration page or a Core-team review.

The official [`brands` rule](https://contributors.home-assistant.io/core/quality-scale/brands/)
requires assets in the Home Assistant Brands repository and lists no exceptions.
For custom integrations, Home Assistant's separate
[brand-image guidance](https://contributors.home-assistant.io/core/integrations/brands/#brand-images-for-custom-integrations)
requires local `brand/` images from HA 2026.3 onward. We mark `brands` as `done`
for that custom-integration equivalent: the package contains project-owned
icons and logos, checked by the [legal/provenance gate](../scripts/check_legal_audit.py).
This is **not** a claim that our assets have been accepted into the upstream
Brands repository. A future Core submission would require that separate step.
See [asset provenance](legal.md#project-owned-generic-artwork).

## Exempt rules

Only these four rules are exempt; the same reasons are recorded in the YAML:

- `docs-conditions`: the integration defines no custom automation conditions.
  Automations can use HA's standard entity-state and numeric-state conditions.
- `docs-triggers`: the integration defines no custom automation triggers.
  Its blueprints and command-result event examples use HA's standard triggers instead.
- `dynamic-devices`: one config entry represents one wallbox. It does not
  discover or add child devices at runtime.
- `stale-devices`: the entry has no dynamically discovered child devices to
  remove. Removing the entry uses HA's normal device/entity lifecycle.

The two documentation exemptions follow the official
[conditions](https://contributors.home-assistant.io/core/quality-scale/docs-conditions/)
and [triggers](https://contributors.home-assistant.io/core/quality-scale/docs-triggers/)
rules. The other two concern integrations that add and remove devices within
an existing entry.

## Reproducible checks

Run the [maintenance validation](validation.md) for the exact checkout:

```bash
.venv/bin/python scripts/check_validate.py
```

Required CI validates the supported **minimum and latest stable HA** versions.
Latest stable also runs strict typing; minimum HA uses `--skip-typing` because
HA's flow/action type annotations changed in 2026.10. Scheduled preview checks
are additional compatibility evidence. See the [support policy](../SUPPORT.md).

The suite enforces **at least 99% overall**, **more than 95% per production
integration module**, and **100% in the six configuration-flow modules**,
counting statements and branches together. Checks use unrounded counters and
report the remaining gaps; see [coverage requirements](validation.md#coverage-requirements).
Neither these percentages nor this badge mean every possible fault has been tested.
Ruff, dependency checks, legal/provenance checks, security scanning and the
required security-review gate complement behavior tests. Their current status
is visible in the PR and workflow results, not inferred from this page.

Behavior tests cover unlimited-current opt-in, refusal of the invalid
0.01–5.99 A range, pause preservation, register-write intervals, serialized bus
access and heartbeat recovery. Real HA lifecycle and HTTP tests cover service
access, reconnection and unloading. Recorder tests check state-change filtering,
statistics and retention; see [database validation](recorder-validation.md).

[Identity-binding tests](../tests/test_identity_binding.py) verify that a known
wallbox is not silently replaced through setup or service snapshots.
[Service-freshness tests](../tests/test_service_freshness.py) check command refusal
for unavailable or stale data, including requests waiting for the command lock.
The service API's identity precondition and its limits are documented in the
[technical reference](service-reference.md#commands-and-api-boundary).

## What this assessment does not prove

The owner reported successful operation on an **Amtron 4You 310 11 C2** on
2026-11-03. That report did not record a firmware version or verify every
feature. It does not validate later code changes, other models or physical LAN
gateways. The [compatibility matrix](compatibility.md) records that boundary.

Automated tests use simulated wallbox replies. A physical installation still
needs the [quick-start checks](quickstart.md) and the manufacturer's electrical
and [fallback requirements](safety.md). A separate service survives HA Core
restarts, but its own restart, host failure or lost bus connection can interrupt
the heartbeat. The quality badge makes no charging-availability guarantee.

GitHub rules, catalog updates and published artifact provenance require live
checks at release time. Local validation alone does not prove publication or
installation on a user's Home Assistant instance.
