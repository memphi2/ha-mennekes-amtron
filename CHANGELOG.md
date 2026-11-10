# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## 0.8.1 — 2026-11-10

- **Session duration** is shown in hours and minutes, e.g. **1 h 15 min**.
  0.8.0 meant to show it as hours:minutes:seconds, but Home Assistant formats
  a duration only in minutes, hours or days and kept showing seconds. A sensor
  created earlier keeps seconds; choose **h** as its unit in the entity
  settings.
- **Virtual Plug & Charge and automatic selection** recognize a car plugged in
  long after parking. A parked car sends no new position, so after 30 minutes
  its location no longer counted and the wallbox stayed locked. Now a plug
  report within two minutes of the wallbox seeing the car is enough together
  with the car's last known position at home. A fresh position elsewhere still
  excludes the car. The same two minutes absorb small clock differences
  between the car and the wallbox.

## 0.8.0 — 2026-11-09

Stable release of the integration and app. Changes since **0.6.1**. In service
mode, update the app to **0.8.0** first, then the integration.

### Vehicles

- **Vehicle profiles:** keep several cars, each with its own charge level and
  limit, battery capacity, departure, charging losses, detection signals,
  percentage target and learned charging phases. Switch the car manually or let
  the integration recognize it; switching never reloads the integration.
- **Guided setup:** adding or opening a profile walks through all its settings
  in one guide. A profile linked to a car in BavarianData uses the car's data
  right away, and every settings area names the entities it uses. Departure
  timers from the car are offered only when the car sends them.
- **Automatic vehicle selection** from cable and home signals: BavarianData's
  plug or charging-port status and GPS position, or any HA entities, including
  a text sensor for the plug. A car's target, departure plan and phases apply
  only once it reports itself plugged in at home after the wallbox was empty,
  and only while its reports stay consistent with the charge.
- **BavarianData:** the predicted charge level is used while charging; the
  measured one remains the fallback.

### Charging

- **Virtual Plug & Charge** (optional, off by default): releases charging for
  a recognized car, with its own wallbox switch and a status that names why
  charging is blocked. Switching **Charging release** on releases the current
  or next connection by hand for any car, for example a guest, until unplugged.
  The release survives HA and app restarts; manual pauses, locks and reached
  targets stay in force.
- **Switch off charging release when unplugged** (optional): the next car needs
  an explicit release.
- **Departure planning starts late and finishes on time:** it waits until the
  remaining time at full current plus a 30-minute reserve is needed, charges at
  full current and finishes at 6 A. Each profile remembers the phases of its
  last charge. New sensor **Planned charging start**.
- **Percentage targets:** a target at the car's own limit follows that limit and
  lets the car end the charge itself; a target below it pauses the wallbox, but
  only with a car plugged in. Each car keeps its own target and departure.

### Operation

- **Waveshare LAN adapters:** guided detection and setup with optional TCP
  login, verified settings and rollback on failure.
- **App Debug log tab:** live decoded Modbus reads, writes and heartbeats, in
  memory only and limited to ten minutes. The app's connection page works on
  Supervisor 2026.10.4 and newer.
- **Steadier entities:** a single disturbed Modbus read no longer makes
  entities unavailable for a moment. **Connection quality** counts retried and
  failed transactions separately, and lost replies are no longer logged as
  errors.
- **Repairs** notices for sustained heartbeat or service connection failures.
- **Settings** are split into Wallbox, Vehicles and Charging costs; the price
  and German network tariffs are chosen separately.
- **Control result** names the command in its event type, e.g.
  `pause_executed` or `set_current_failed`. **Session duration** shows
  hours:minutes:seconds.
- More robust target storage, reconnects, phase changes and recovery; queued
  departure commands are cancelled when the car disconnects.

### Breaking

- Automations that filter the **Control result** `event_type` for `failed`,
  `uncertain` or another outcome must filter the new `result` attribute.

## 0.6.1 — 2026-11-07

- Add next-trip charging deadlines with fixed or dynamic battery capacity,
  manual/calendar selection, optional BavarianData timer import and climate
  blueprints. Keep departure available for climate after reaching the charge target.
- Import German Module 3 tariffs with optional annual refresh, multiple daily
  windows and marked provisional values, without restarting Modbus.
- Fix switching from Module 3 back to Module 2 or no adjustment, fragmented
  tariff downloads and current HA device-registry access.
- Expire queued timed-plan commands in the service even while HA is offline,
  and update shared tariff validation. Service mode now requires app/service
  **0.6.1 or newer with API 2**: update it first. The stable app is offered through
  **#apps** and reaches existing **#apps-beta** installations too.

## 0.6.0b1 — 2026-11-06

Beta release of the integration and app. **0.5.1 remains stable**; the app beta
is available only through **#apps-beta**.

- Set an optional energy target in kWh or a vehicle charge-level target in percent.
  Targets only limit charging; they never start a charge or lift a deliberate pause.
  Energy goals recover automatically after a temporary meter outage.
- Estimate charging losses from BavarianData history or a selected vehicle-energy
  sensor, with a weighted average. Estimate charging costs from fixed or dynamic
  prices, including optional network tariffs. All estimates are optional.
- See clearer charging and connection status, cancel queued changes and optionally
  trigger automations from command results.
- Configure charging-state waits in seconds, see pending changes and wait for the
  vehicle to confirm a pause before changing phases.
- Set up the app through a guided USB/LAN page with an adapter dropdown. Switching
  to LAN no longer fails because an old USB adapter is missing.
- Improve reconnect and heartbeat-error recovery while preserving deliberate
  pauses and the latest requested current; correct vehicle-presence reporting
  during wallbox faults.
- Add AppArmor protection for the service, clearer entity grouping and icons,
  and quieter Recorder defaults. Optional estimates add no extra Modbus polling
  or growing session history.

**Updating:** keep your existing entry and app data. Update the app to **0.6.0b1**
from **#apps-beta** first to use the new service features, then select the
integration prerelease in HACS/Marketplace and restart
Core. Older API 2 services from **0.5.0** remain compatible with their existing
functions. The app's connection page requires stable **Supervisor 2026.11.0+**;
the integration still supports **Home Assistant 2026.9+**.

## 0.5.2b2 — 2026-11-06

- Persist executed queued stop intent before refreshing wallbox status, so a
  failed or cancelled read cannot leave stale intent for the next service start.
- Add a configurable interval between charging-state changes, in seconds,
  defaulting to 300. Queue early pause/resume, phase, release and lock changes,
  with the latest request replacing an older pending action.
- Show the pending change and its scheduled time without per-second Recorder
  updates. Configure the interval in direct-mode HA options or in the app/service
  that owns the connection; heartbeat and same-state error recovery continue.
- Confirm the vehicle has paused before sending a phase change. Abort the phase
  command if confirmation fails or times out, retaining the requested pause.
  Add a separate configurable grace time after confirmation, default 5 seconds,
  and recheck the state before switching.

- Skip the long transition wait and vehicle grace time only when fresh EVSE
  and CP status confirm no vehicle is connected. Detect unplugging during
  pending waits; retain five-second write floors and heartbeat.

## 0.5.2b1 — 2026-11-06

- Verify a known physical wallbox identity after reconnects and before control
  writes. Reject changed or missing serial numbers without altering saved
  charging intent, and recover when the original wallbox returns.
- Ignore malformed optional device text without breaking initialization or
  polling, and decode NUL-terminated strings without interpreting unused bytes.
- Discard an older deferred current target when a newer write loses its reply
  or is cancelled. Preserve the latest explicit target through recovery and
  service restarts instead of later restoring the superseded value.
- Retry automatic heartbeat recovery when error 200 returns during its final
  check, even if the preceding fresh check needed no recovery writes.
- Retain confirmed heartbeat and fallback repairs when their status register
  temporarily cannot be read; clear them only after a healthy reading.
- Treat excessively nested service responses as invalid and mark the connection
  unavailable instead of leaving cached availability after a decoder error.
- Show exactly three readable app connection choices. Older technical choices
  need a one-time selection of their matching readable name; standalone
  configuration files keep accepting the technical names.
- Add a custom AppArmor profile for the Supervisor app, with limited runtime and
  state-file access, plus a dedicated enforcement check using simulated USB/LAN
  transports on native AMD64 and ARM64 runners before publishing an app image.

## 0.5.1 — 2026-11-05

- Release the Modbus app independently when its service changes. Integration
  updates accept compatible service versions from the declared minimum; the
  existing 0.5.0 API 2 app can keep its heartbeat running without an app update.
- Show the LAN adapter's host/IP field directly in the app configuration and
  offer readable USB/LAN connection names. Existing saved connection values
  remain valid; LAN requires an address while USB leaves it blank.

## 0.5.0 — 2026-11-05

- Release the optional independent Modbus service as stable. It keeps heartbeat
  and the selected charging state, including pause, through HA Core restarts.
  HA OS discovers the app and its credentials automatically; direct connections
  remain supported. The app's Read-only setting controls service write access.

- Add LAN-to-RS485 adapters to both direct Home Assistant connections and the
  independent Modbus service. Select transparent RTU-over-TCP or a translating
  Modbus TCP gateway, with separate host, TCP port and wallbox address settings.
  USB remains available; LAN installations do not require a local serial device.
- Preserve the shared heartbeat, write limits and recovery behavior across
  transports. Keep uncertain writes from being retried, and redact network
  endpoints from downloaded diagnostics without adding Recorder activity.
- Document gateway setup, transport selection, network access restrictions and
  migration while retaining existing wallbox identities and control settings.
- Provide English, German, French, Dutch and Italian in the integration and app,
  with an updated installation guide and HACS/Marketplace installation buttons.
- Recover expired service credentials through Home Assistant reauthentication,
  with automatic Supervisor lookup for apps and a token prompt for standalone
  services. Retain the existing entry, options and entity identities.
- Complete the 54-rule Platinum self-assessment with current evidence, named
  maintenance ownership and an explicitly self-assessed README badge. Enforce
  full setup-flow coverage and more than 95% coverage of each integration module
  alongside the existing 99% global threshold.
- Remove obsolete 0.1.0 migration instructions, the empty migration hook and
  unused constants. Home Assistant still rejects incompatible entry schemas.
- Reject a different wallbox identity during setup and in running service
  snapshots. Stop exposing controls until the expected wallbox returns, without
  changing registered devices or replaying commands.
- Bind every service command to its expected wallbox identity, checked before
  any write. The first stable service API uses `/v2` endpoints and rejects old
  beta commands without that precondition. Update the service first, then the
  integration; existing configuration and saved charging intent are retained.
- Reject service commands when cached wallbox data has expired, including
  requests that become stale while waiting for another command.
- Clarify USB/LAN handover, app updates, heartbeat startup and control intervals
  in the documentation. Keep one Marketplace installation button and a separate
  HACS availability badge.
- Improve reconnect cleanup, pause-current preservation after a lost reply,
  slider updates after service limit changes, setup validation and diagnostics.
  Existing beta apps receive the stable app
  update in place, preserving their configuration, credentials and saved intent.

## 0.5.0b5 — 2026-11-05

- Make the app's Read-only switch the sole write-permission setting for service
  connections. Remove the duplicate HA option, ignore old HA-only mode settings,
  and keep control entities registered with stable IDs. A fresh writable service
  response enables them; read-only, disconnected or unavailable service state
  blocks commands. Permission changes notify HA even when register values are
  unchanged, without HA sending commands or adding per-poll Recorder history.
- Rewrite the user documentation around installation, daily charging and
  troubleshooting. Present the optional service as the preferred connection,
  explain which settings belong in the app or integration, and move API,
  deployment and validation details into dedicated reference pages.
- Add French, Dutch and Italian translations for the integration and app,
  including setup, controls, errors and repairs. Publish all app translations
  in the catalog and check translation keys, placeholders and packaged files.
- Unload the platforms belonging to the active runtime when connection settings
  or control mode change. Avoid ignored-unload warnings on current HA, failed
  unloads on minimum HA and leftover active controls after disabling master mode.

## 0.5.0b4 — 2026-11-05

- Keep app setup available when the wallbox reports an unusable optional maximum
  EVSE current. Treat that capability as unknown while retaining its raw reading;
  configured command limits remain enforced. Accept these readings from already
  running beta-3 apps without requiring an app restart.

## 0.5.0b3 — 2026-11-05

- Refresh pending app-discovery credentials from Supervisor when confirming or
  retrying setup, so a missed token-change announcement cannot leave the flow
  permanently using a stale token.

- Reuse Supervisor app discovery when selecting the service connection manually;
  URL and token fields are reserved for separately hosted services. Keep wallbox
  identity checks during migration and distinguish authentication, unavailable
  wallbox data, invalid API responses and network failures in setup messages.
- Accept unknown uint16 hardware phase codes as readings without exposing phase
  switching controls; an unfamiliar optional capability no longer rejects the
  entire service connection.

- Supervise the service heartbeat task: an unexpected exit now stops the service
  and releases the serial port, allowing Supervisor's watchdog to restart it.
  Ordinary missed replies still use the existing heartbeat retry cadence.

- Include the independent service's connection, heartbeat and recovery timeline
  in HA diagnostics instead of the separate HA client's local event history.
- Release unloaded serial clients promptly by avoiding a client-factory reference
  cycle.

- Add bounded, in-memory write diagnostics with bus-wait/transaction timing and
  packet callback counts for failed writes and subsequent confirmations. Fetch
  service diagnostics only on demand; add no Recorder state or persistent logs.
- Clarify that missing heartbeat acknowledgements leave delivery uncertain,
  and document how to investigate isolated timeout messages.

## 0.5.0b2 — 2026-11-04

- Log service/API/serial/heartbeat lifecycle and health transitions, name invalid
  configuration fields, and close serial ownership on unexpected worker exits.
- Accept the baudrate strings produced by Supervisor selection fields; valid
  saved app options no longer terminate the service with a configuration error.
- Discover the Supervisor app automatically with its address and token, reuse
  existing wallbox entries, and manage persistent private app tokens automatically.
  Remove the beta-1 manual app-token option without a legacy fallback.

## 0.5.0b1 — 2026-11-04

### Added

- Independent Modbus service with a shared HA-free driver, authenticated local
  API, durable control intent, Python package and local Supervisor app/container
  build. It keeps the heartbeat and last charging state through HA Core restarts,
  including indefinite HA outages; reconnecting HA sends no charging commands.
- Service setup and reconfiguration preserve existing device/entity identities.
  Direct serial mode remains available. The service is distributed as a beta app and multi-architecture container,
  with a separate stable catalog and no automatic migration of existing entries.

### Fixed

- Pin app installations to the attested container digest, preventing later
  registry tag changes from substituting a different image.
- Bind container attestations to the validated native build digests, including
  existing version tags and tag-replacement races; matching OCI labels alone
  cannot authorize publication.
- Sync the saved control-intent file and its containing directory before
  acknowledging a successful service command; failed syncs remain retryable.
- Wait for the wallbox to confirm manual heartbeat recovery for up to ten seconds,
  avoiding a premature "fault still active" message while firmware clears error
  200. Confirmation reads do not repeat charging commands or lift a pause.
- Apply the HA current cap at the service's write boundary when a remote resume
  target is unknown or has changed since HA last polled, and update
  remote write-rejection repairs even when all polled register values are unchanged.
- Recover error 200 while charging is paused, locked or explicitly not released.
  Both automatic recovery and the manual repair reassert the existing controls
  instead of enabling charging or forcing a temporary current of at least 6 A.
- Restore deliberate pause intent across entry reloads and Home Assistant
  restarts, independently of changed wallbox readback, and keep the saved
  resume current. No additional Recorder attribute or heartbeat history is added.
- Keep an uncertain pause request after a lost acknowledgement or cancellation;
  discard older queued current writes that could otherwise undo that pause.

## 0.4.1 — 2026-11-03

### Added

- Recover error 200 automatically in master mode once heartbeat delivery
  resumes, with at most three attempts per persistent fault. Respect pause,
  device lock, explicitly disabled charging release, current limits and the
  five-second write floor; restore an
  unlimited setting through the existing replaceable command queue.
- Add a diagnostic Heartbeat problem sensor in master mode. It detects a
  missed ten-second acknowledgement deadline or stopped task independently
  of polling, without per-heartbeat Recorder updates.

### Fixed

- Remember an explicitly disabled charging release across entry reloads and
  Home Assistant restarts. Automatic recovery cannot override it, including
  after an unconfirmed block command or a failed enable command.
- Read the error and current before recovery. A healthy wallbox receives no
  recovery commands, other faults are rejected and a missing saved setpoint
  no longer raises a live 6 A limit to the device maximum.
- Clear write repair issues when the affected command succeeds, while retaining
  historical diagnostic counters. Repair dialogs now handle connection errors,
  write cooldowns and read-only mode explicitly, verify recovery and close
  already-resolved heartbeat issues without sending more commands.

### Changed

- Show the charging-current limit as a slider, retaining 0 A for no limit and
  the separate charging-pause switch.

- Allow pause and resume after the five-second current-write interval instead
  of imposing a five-minute manual-control lockout. Phase switching keeps its
  own five-minute guard; PV automation hysteresis retains its six-minute default.

## 0.4.0 — 2026-11-03

### Fixed

- Disable dependency caching throughout tag-release validation and publication
  so a moved tag cannot feed cached executables to the privileged publisher.
- Use Mennekes Amtron as the default device name, including existing default
  titles; preserve custom names and registered entity IDs.
- Remove leading underscore padding from displayed serial/article numbers and
  map the verified 1313201205 article to Amtron 4You 310 11 C2. Keep existing
  device identifiers and entity unique IDs unchanged.
- Isolate package staging per build and replace the output ZIP atomically;
  overlapping or failed builds cannot leave a partial archive.
- Reject growing or missing listener counts in loaded and continuous soak
  measurements, including peaks that disappear again before unload.
- Check recovery cooldowns before writing heartbeat or charging release,
  so an already blocked current command cannot leave a partial recovery.
- Confirm a fresh idle state before restarting the wallbox; missing, invalid
  or non-idle device status prevents the restart command.
- Preserve the pre-pause current across entry reloads for resume and recovery,
  while still applying the current cap configured after the reload.
- Retry failed heartbeats on a one-second interval so a brief serial reconnect
  does not consume the entire ten-second delivery deadline.
- Retain continuous-soak measurements on failures and timeouts, and upload
  diagnostic reports even when the CI test fails.
- Verify the downloaded SBOM dependency inventory, versions, extras and
  relationships against the approved manifest even when checksums are consistent.
- Read a fresh charging state before switching phases; reject unknown status
  instead of skipping the required pause based on missing or stale readback.
- Ignore old current readback from polls that overlap a write, including
  uncertain or cancelled writes, so a delayed poll cannot lift pause protection.
- Preserve write intervals and pause/resume hysteresis across entry reloads.
  On first setup or a full HA restart, guard current writes for five seconds
  and pause/resume/phase changes for five minutes while timing history is unknown.
- Measure register write intervals after transport completion, including
  delayed, uncertain and cancelled transactions.
- Retain pause protection when readback or a write acknowledgement is lost.
- Verify release drafts before publishing; never replace published assets.
  Generate reproducible metadata and SBOM identifiers from the source commit.

### Added

- Include the Amtron 4You 310 11 T2S socket variant, article 1313201605/BK,
  in model recognition and the compatibility matrix alongside Start 2.0s T2S.
- Allow 0 A in the charging-current number input to disable the EMS limit;
  display unlimited mode as 0 and preserve it across pause/resume and reloads.
  Reject values between 0 and 6 A and retain write and pause/resume guards.
- Recognize all documented variants of the three supported RTU families,
  with a sourced model matrix and a separate user-reported hardware-test status.
- Run the continuous serial soak with real SQLite Recorder: measure row and
  attribute growth, queue backlog, database/WAL sizes and heartbeat gaps while
  reconnects overlap purge/repack; verify actual VACUUM and database integrity.
- Suggest Recorder exclusions with actual registered entity IDs, including
  renamed sensors and multiple wallboxes, without modifying configuration or history.
- Measure continuously loaded runtime resources; run an hourly-duration soak
  on weekly/manual CI alongside the existing lifecycle and Recorder tests.

### Changed

- Record the owner's successful hardware test of the released integration on
  Amtron 4You 310 11 C2 separately from automated and unreleased validation.
- Exercise real serial transport reconnects, changing readings and delayed
  replies in the continuous soak; check maximum heartbeat gaps, not only totals.
- Type the Modbus transport boundary and share write timing state across reloads.
- Cancel superseded PR validation, add job time limits and cache dependency
  downloads. Required HA validation remains minimum plus latest stable.

## 0.3.0 — 2026-11-02

### Added

- Include the last 40 connection, heartbeat, recovery and rejected-write events
  in diagnostics. The history lives only in RAM, resets on reload and adds no
  entity attributes or Recorder state changes.
- Measure repeated HA setup, recovery, idle and unload cycles, including task,
  listener, client, memory and serial-descriptor cleanup.
- Verify published release assets against the approved Git tree, checksums,
  metadata and SBOM; audit public GitHub text and release assets for likely
  identifying values without printing matches.

### Changed

- Validate PRs and releases against the fixed minimum and automatically resolved
  latest stable HA. Keep prerelease checks for weekly/manual maintenance only.
  Migrate the retired required-check names while retaining the security gate.
- Guard owned code against known deprecated APIs and blanket warning suppression.

### Fixed

- Reject Modbus replies for the wrong function and write acknowledgements with
  the wrong register address, value or count. Mismatched replies cannot claim
  write success or release the cooldown for an uncertain write.
- Verify fragmented, corrupt, truncated, foreign-device and delayed RTU replies
  with the real serial library. Close virtual serial relay threads and all
  descriptors after tests.

Existing 0.2.x config entries, devices and entity IDs remain valid. The minimum
Home Assistant version remains 2026.9.0. Upgrades from 0.2.3 need no blueprint
or config-entry migration; older releases retain their documented upgrade steps.

## 0.2.3 — 2026-11-01

### Upgrade action

- Reload existing **Charge from PV surplus** automations after the updated
  blueprint is installed so they follow the latest power budget. Apply the
  changes manually to edited blueprint copies. When upgrading from 0.2.1 or
  earlier, also select the **Charging paused** input introduced in 0.2.2.

### Changed

- Run weekly and manual HA compatibility checks without opening routine
  dependency PRs. Check the pymodbus version resolved by each HA installation,
  including Core's dependency pin and package constraints.
- Verify repeated Recorder purge/repack cycles, preserved hourly statistics
  and database integrity with full and lean profiles; document disk monitoring.
- Explain bus-search duration and the heartbeat interruption during
  reconfiguration, including the wallbox's fallback behavior.
- Require a clean Codex Security Review of the current PR head before merging;
  verify bot/App identity and reject stale, incomplete or failed evidence.
  New or edited review requests invalidate old approval; a pending review
  blocks merging without causing a spurious publisher-job failure.
- Refresh the legal/provenance audit and quality evidence, retaining the old
  snapshot in Git history and stating hardware, coverage and policy limits.
- Reuse the block decoder in the Modbus read path and remove an unused heartbeat
  type alias.
- Run the complete Python suite once per validation job with coverage enabled,
  removing the duplicate test pass while retaining the 99% gate.
- Notify entities only when polled data or availability changes; report write
  repair issues immediately even when device registers remain unchanged.
- Provide a complete low-history Recorder profile and a reproducible full/lean
  replay comparison while retaining live sensors and energy/power statistics.

### Fixed

- Disable pymodbus transport retries for writes so missing replies cannot
  bypass register cooldowns. Preserve the bounded retry budget for reads.
- Propagate cancellation wrapped by pymodbus, stopping bus searches and
  releasing the serial port while retaining uncertain-write cooldowns.
- Keep the accepted resume current after a rejected setpoint. Pausing for a
  phase change remembers the actual current before publishing the paused state.
- Replace an in-flight PV current adjustment when a newer surplus value
  arrives instead of dropping that update.
- Preserve existing GitHub branch protection and check App IDs when applying
  repository settings; add a read-only required-check drift check.
- Keep the integration available when optional blueprint files cannot be read
  or written; preserve local edits and replace updated files atomically.
- Check the phase-switch cooldown before pausing a running charge, and serialize
  the complete sequence against other current and phase requests.
- Retain register write intervals after a lost reply, device failure or
  cancellation; only definite rejections allow an immediate retry.
- Reconfigure through the existing serial client when settings are unchanged;
  release the exclusive port for changed settings and searches, restoring the
  original entry after a failed or cancelled attempt.
- Enforce Home Assistant control permissions for the charging-current action,
  including unlimited mode, before writing to any targeted wallbox.
- Disable automatic routine Dependabot PRs for GitHub Actions and Python;
  retain grouped security updates and vulnerability alerts. The quarterly
  schedule introduced in 0.2.2 still allowed immediate checks after changes
  to the Dependabot configuration. No integration update is required.

## 0.2.2 — 2026-11-01

### Upgrade action

- Existing **Charge from PV surplus** automations must select the wallbox's
  new **Charging paused** input, then be saved and reloaded. Until configured,
  they load but send no current commands. Edited blueprint copies need the
  changes applied manually. See [Blueprint updates](docs/blueprints.md#updating-existing-blueprint-automations).

### Changed

- Use **Mennekes Amtron RTU** as the Marketplace/HACS display name and setup
  title. Add the direct Marketplace repository button to the README.
- Group routine dependency updates quarterly with one open PR per ecosystem;
  keep security updates separate and enabled in the repository settings.
  Remove the pip-compile input that caused unwanted transitive Python pins.
- Rewrite end-user setup, daily controls, automation and troubleshooting
  guidance. Correct entity IDs and action fields, explain phase-switch pauses,
  and put Recorder retention/exclusion choices before technical measurements.
  Remove unsupported physical-validation and whole-house protection claims.
- Exercise the built-in Marketplace installer offline: first install, update
  restart requests, rollback of an invalid package and coexistence with TCP.
- Document Marketplace installation, existing entries, updates and removal,
  retaining HACS instructions for Home Assistant 2026.9 and 2026.10.
- Declare the `energy.ev_charging` category for Marketplace discovery.
- Add Home Assistant 2026.11.0b1 to the fixed validation and release matrices
  alongside 2026.9.0 and 2026.10.0, retaining rolling next-version checks.
- Type-check platform device classes on both October and November Home
  Assistant without obsolete ignore comments or changing the runtime imports.
- Validate hourly energy/power statistics and Recorder exclusion profiles
  against real SQLite; document November statistics metadata and optional
  history restricted to total energy and total power.

### Fixed

- Let PV control initialize an unknown current setpoint when the wallbox is
  available and its current cap is valid, including unlimited mode without a
  restored setpoint. Unavailable controls remain excluded.
- Recalculate the PV current after resume, vehicle connection and phase
  changes, even when the surplus remains constant. A paused charge still
  receives no current writes.
- Report phase-switch rate limits as translated Home Assistant errors with
  the remaining wait time, while preserving the five-minute write floor.
- PV blueprints accept W/kW and skip invalid inputs. Current adjustment now
  requires an unpaused wallbox and respects the configured entity cap. Existing
  current automations must select the new Charging paused input; otherwise
  they send no commands. Use a hold time above five minutes (new default: six).
  Real Home Assistant automation tests cover the behavior.
- Close probe transports when opening the serial port fails or is cancelled,
  so a search does not retain resources between candidates.
- Remove an entry's repair issues when the entry is deleted, preserving them
  during reloads and retaining issues belonging to other entries.
- Correct documentation and setup/options help: read-only still transmits
  Modbus requests and cannot share an RS-485 bus with another master.
- Align the support policy's hardware claims with simulated RTU validation.
- Reconfiguring bus parameters reloads the entry once through its update
  listener, avoiding the duplicate reload deprecated for Home Assistant
  2026.12. Submitting unchanged parameters on a loaded entry leaves the
  connection running; an unloaded entry is started by reconfiguration.

## 0.2.1 — 2026-10-24

### Changed

- Publish one installation archive for HACS; remove the separate manual ZIP.

- Separate measurement interval, default 30 seconds, reduces Recorder writes
  while status and controls retain their five-second default. Existing entries
  also use 30 seconds unless configured otherwise. EVSE state transitions
  immediately refresh measurements and lifetime counters.
- Document measured Recorder load, retention, optional detail exclusions and
  the distinction between simulated validation and physical hardware testing.

### Fixed

- Accepted current and pause commands supersede older deferred setpoints;
  deferred writes are serialized with new commands and awaited on shutdown.
- The five-second current-write floor also applies during error recovery.
- Error recovery only applies pause/resume hysteresis when it actually lifts
  a pause; repeated recovery after the five-second floor remains possible.
  A rate-limited recovery reports failure without deferring its final write.
- Rejected platform unloads keep the connection and heartbeat alive; failed
  setup releases both. Register fallback reads allow heartbeat priority.
- Diagnostics omit serial-number words and redact identifiers from errors.
- Invalid non-finite and fractional enum writes are rejected before encoding.
- Failed-block diagnostics survive skipped polls until a successful retry.

## 0.2.0 — 2026-10-23

### Breaking changes

- The integration domain is now `mennekes_amtron_rtu`, with files under
  `custom_components/mennekes_amtron_rtu/` and the display name
  **MENNEKES AMTRON RTU (Unofficial)**. This avoids the domain collision with
  the separate MENNEKES AMTRON Modbus TCP integration.
- Existing 0.1.0 installations require reconfiguration. Follow the
  [upgrade guide](docs/upgrading.md) before updating; it covers config entries,
  entity/device references, action calls and blueprint automations.
- The charging-current action is now
  `mennekes_amtron_rtu.set_charging_current`, and bundled blueprints install
  under `blueprints/automation/mennekes_amtron_rtu/`.

### Changed

- Numeric sensor values are rounded to their declared precision to reduce
  unnecessary Recorder updates from floating-point conversion noise.
- Vehicle measurement blocks use a one-minute polling cadence while the
  wallbox explicitly reports itself idle. Connected and unknown states keep
  the configured polling interval; entities retain their values between reads.
- HACS installation instructions identify the RTU integration by name and
  include a custom-repository fallback while catalog inclusion is pending.
- Updated validation tooling and GitHub Actions. Validation locks are checked
  to keep Home Assistant in control of its transitive dependencies.

## 0.1.0

Initial release.

A local Home Assistant integration for MENNEKES AMTRON wallboxes over Modbus
RTU, built against the manufacturer's Modbus RTU specification revision 2.5
(register layout v01.03) and developed for an AMTRON 4You 310 11 C2.

> **Not yet verified on hardware.** Every value, rule and limit in this release
> comes from the specification and is exercised against a real Modbus RTU
> server over a virtual serial link. None of it has been measured on a real
> wallbox. Start in read-only mode and work through the verification steps in
> `docs/quickstart.md`.

### Requirements

- **Home Assistant 2026.9.0 or newer**, validated against 2026.9.x and
  2026.10.x. Home Assistant replaced voluptuous with `probatio` in 2026.9 and
  types its own config-flow and action signatures against it from 2026.10.
  This integration imports `probatio` directly, as Home Assistant Core does.
  Releases older than 2026.9 ship no `probatio` and are not supported.
- **pymodbus 3.13.1 or newer**, the version every Home Assistant release in
  that range resolves.

### Setup

- Config flow that opens the serial port and reads the Modbus layout version
  and serial number before an entry exists, so a wrong port, address or bus
  parameter fails during setup instead of producing an empty device.
- The form **searches the bus** when the values entered do not work: every
  documented device address at those bus parameters, or every documented
  combination of address, baud rate and frame. It runs as a progress step with
  an estimate, tries the user's own values first, and only reads. A single
  candidate whose port will not open is skipped rather than ending the
  search; five failures in a row report the port, not the wiring.
- Five fields, not seven: the device address is a number box, and the frame is
  one choice of the three the specification documents, so impossible
  combinations like 8N1 are unreachable.
- Reconfigure flow for the bus parameters, pre-filled from the entry. A failed
  connection test keeps what was typed.
- Options for the control mode, the polling interval and a charging-current
  cap.
- A new entry is **read-only**: it polls and creates sensors, but never writes
  and sends no heartbeat. Control entities appear only after switching the
  control mode to Modbus master.

### The device

- 36 sensors, 9 binary sensors, 1 number, 3 switches, 2 selects and 2 buttons,
  all translated to English and German including every enumerated state.
- Capability gating by register layout version (`v01.00`–`v01.03`) and by the
  hardware phase option: a register the firmware does not have produces no
  entity, rather than an entity stuck at `unknown`.
- 18 declared contiguous read blocks with a single-register fallback when a
  device rejects a range read. Eight of them carry configuration and are read
  once a minute instead of every poll, which keeps the bus for the ten that
  move. A block that was read and failed takes its entities unavailable
  instead of leaving the last reading in place, so a frozen measurement can
  never pass for a live one.
- `sensor.*_energy_total` feeds the Energy dashboard. The manufacturer marks
  that register as not usable for billing, and the entity says so.

### Safe control

- One write path. `0x0302` means three different things depending on the
  value: `0` removes the limit and makes the wallbox signal its **maximum**,
  `0.01`–`5.99` is the documented pause, `≥ 6` is a real limit. The number
  entity never reaches the first two; pausing has its own switch, and "no
  limitation" needs an explicit opt-in on the action.
- A dedicated heartbeat task writes `0x0D00 = 0x55AA` every five seconds in
  master mode, decoupled from the data poll, because a slow poll must never be
  able to starve it. It sleeps only the remainder of its interval, so time
  spent waiting for the shared bus is absorbed rather than added, and a gap
  the wallbox would not tolerate is logged and counted.
- One `asyncio` lock for every transaction, because RS-485 is a single-master
  bus — and a queue gate in front of it that only the heartbeat may skip, so
  a stalling device cannot push the heartbeat behind every other bus user.
- The manufacturer's rate limits are enforced, and the two that meet on
  `0x0302` are kept apart: every write to it keeps five seconds from the one
  before, which belongs to the register, while pausing and resuming keep five
  minutes, which belongs to the operation. A charging-current change that
  comes too fast is deferred and the newest value written; a pause or resume
  is refused with the remaining time. While the pause value is active, any
  accepted current counts as a resume.
- A phase switch pauses a running charge first, as the manufacturer's own
  sequence does. Restarting the wallbox is refused unless it is idle.
- The §14a EnWG downgrade is enforced in the wallbox hardware. The integration
  makes it visible through `binary_sensor.*_downgrade_active` and the
  downgrade-current sensor, and never tries to work around it.

### Automations

- Four blueprints, installed into `config/blueprints/automation/` when the
  integration is set up: charge from PV surplus, pause and resume on PV
  surplus with hysteresis, recover from a lost heartbeat, and notify on a
  grid-operator downgrade. The installer never replaces a blueprint that was
  edited.
- `mennekes_amtron.set_charging_current` with an `allow_unlimited` opt-in,
  for the one argument that cannot be offered safely as an entity.

### Diagnosis

- Diagnostics built from an allowlist: no serial port path, a hashed serial
  number, and the full raw register image, which is what makes a support
  report decidable.
- Four repair issues: a lost energy-manager heartbeat (fixable, runs the
  documented recovery sequence), a missing fallback configuration, an older
  register layout, and a refused write.
- A read-only bus check that ships with the integration, so it runs on the
  machine the USB adapter is plugged into, with Home Assistant's own Python.
  It can search for the device address and the baud rate.

### Quality

- All 52 official Home Assistant quality-scale rules answered: 43 `done`,
  9 `exempt`, none open.
- 368 tests and a 99 percent coverage ratchet. The suite includes an
  end-to-end test over a virtual serial link against a real Modbus RTU server,
  and a smoke test that boots Home Assistant, drives the real config and
  options flows, and runs the blueprints as real automations.
- `mypy --strict` over every module and every script.
- `ruff` with 38 rulesets, including the bandit security rules.
- Validation gates for the repository, legal provenance, the quality scale,
  the register map and the pymodbus requirement, run identically locally and
  in CI against the minimum and current Home Assistant, plus an early-warning
  job that resolves the next Home Assistant pre-release rather than pinning
  one.
- The register-map gate also checks `docs/modbus-registers.md` against the
  code — every address, span, data type, access mode, layout version, unit,
  read block and enumerated value. That document is what a reviewer holds
  next to the manufacturer's specification.
- The typing gate runs against the current Home Assistant, because no single
  source file can satisfy `mypy --strict` against both ends of the supported
  range; the minimum entry runs every other gate including the full suite.
  `scripts/check_validate.py --skip-typing` does the same locally.
