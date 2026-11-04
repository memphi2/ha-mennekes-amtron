# Service and integration publication

One source repository produces the HACS integration and the independently running
Modbus service. A release keeps one HACS ZIP plus its verification assets. Container
images live in GHCR, and generated app metadata lives on `apps-beta` or `apps` in
the same repository. There are no automatic version-update PRs.

## Prepare one reviewed source change

Update the integration manifest, `project-versions.json`, the Python project
version, `modbus_service/config.yaml`, release notes and changelogs together.
Versions use `X.Y.Z` for stable, `X.Y.ZbN` for beta or `X.Y.ZrcN` for candidates.
The app version, Python version and integration version must match.

PR checks run minimum/latest stable HA and native amd64/aarch64 container tests.
Automatic bot review and the mandatory security-review status must pass before
merging. Tag only the validated main commit with `v<VERSION>`.

## Publication order

1. Revalidate the source and require successful canonical main CI.
2. Build native images, exercise their API/authentication and clean shutdown,
   then push unique candidate tags. There is no wallbox connected in these tests.
3. Verify both candidates' platforms and source labels. Create the versioned
   multi-architecture image once. Reruns verify the existing version and never
   replace it with a new build.
4. Attest the image, verify its provenance and check that it is anonymously
   pullable. Only image publishers have `packages: write` permission.
5. Publish and independently download/verify the HACS release. Betas are marked
   as prereleases and never replace the latest stable GitHub release.
6. Publish an allowlist of app metadata, documentation, artwork and license
   notices to the matching catalog branch. This happens after the image and
   GitHub release exist. A catalog cannot move backwards or replace a version.

The catalog updater makes an ordinary commit on its generated branch, without
force and without opening another PR. Source branches remain untouched. Keep
catalog URLs stable: Supervisor derives the app identity from the repository
URL including the branch. Changing between beta and stable is a deliberate
handover between two app installations.

## First GHCR publication

GitHub may create the container package as private even for a public source
repository. In the GitHub package settings for `ha-mennekes-amtron-service`,
set visibility to **Public** once. Anonymous-access verification deliberately
stops publication until this is done; it does not create an unusable app update.
Re-run the failed jobs after changing visibility. Published version tags and
release assets are preserved on reruns.

## Recovery from a failed publication

Read the failing job before retrying. A wrong existing digest/source or an older
catalog version request is a stop condition, not permission to overwrite it.
A genuine correction after publication needs a new version. A network failure,
private first package or interrupted catalog update can be retried on the same
approved source. Never delete/recreate tags to make a check pass.

Stable 0.5.0 follows beta installation and hardware acceptance: deliberate pause,
charging-release blocks, HA-Core restart, service restart, USB recovery and
persistent error-200 behavior. CI does not replace that hardware validation.

The workflow follows Home Assistant's [app publication](https://contributors.home-assistant.io/apps/guides/publishing/)
and [repository/channel](https://contributors.home-assistant.io/apps/guides/repository/)
contracts.
