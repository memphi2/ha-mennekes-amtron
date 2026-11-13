# Service and integration publication

One source repository produces the HACS integration and the independently running
Modbus service. A release keeps one HACS ZIP plus its verification assets. Container
images live in GHCR, and generated app metadata lives on `apps-beta` or `apps` in
the same repository. There are no automatic version-update PRs.

## Prepare one reviewed source change

The integration and app have independent versions:

| Component | Version sources |
|---|---|
| HA integration | Integration manifest and `project-versions.json` |
| Modbus app/standalone package | `modbus_service/config.yaml`, `pyproject.toml` and `device/service_version.py`; these three must agree |

Versions use `X.Y.Z` for stable, `X.Y.ZbN` for beta or `X.Y.ZrcN` for candidates.
Each integration release needs a new integration version and
`v<INTEGRATION_VERSION>` tag. Change the app version only for a relevant app
change, subject to the promotion rule below. The integration tag identifies
the reviewed source; it does not require the service to use the same number.

The HA client declares the required service API and minimum service version
separately. The current requirement is **API 2 and service 0.6.1 or newer**,
which supplies deadline expiry for queued timed charging commands. Raise the
minimum only when the integration needs a newer service feature; a published
or planned service release must satisfy that requirement.

PR checks run minimum/latest stable HA and native amd64/aarch64 container tests.
Automatic code and security reviews remain enabled; assess their findings and
resolve actionable issues before merging. No separate Codex receipt or status
is required. Required CI checks must pass. Tag only the validated main commit.

### Decide whether the app needs publication

The release planner compares the current service inputs with the original
source commit of the last published app. Its relevant inputs are:

- The shared Python driver and service under `device/`.
- `Dockerfile`, `.dockerignore` and `requirements-service.txt`.
- The semantic build, project and setuptools configuration in `pyproject.toml`.
- App configuration, app translations and the custom `modbus_service/apparmor.txt` profile.
- `LICENSE`, `NOTICE` and the app's brand icon.

Integration-only code, tests, repository/app README files, documentation and
changelogs do not trigger an app update. Version values are checked separately
from the content comparison. A real service change requires a new app version;
a missing bump stops publication. A version bump without relevant changes is
also rejected, except for promoting a beta to stable.

When the version and relevant inputs are unchanged, the planner reuses the
published service. It verifies the existing image and catalog without pushing
an image or committing catalog changes. The HACS integration release proceeds
normally. The existing image retains its original source and attestation;
reuse never relabels it as built from the newer integration commit.

Catalog verification compares the complete file inventory, file modes and Git
blob identities with the original app release. An unchanged `release.json`
alone cannot hide a changed Dockerfile, AppArmor profile or configuration.
Missing, modified or extra files stop reuse and same-version publication
retries; they are not silently replaced. Original app documentation is checked
against its own source commit, not newer integration-only documentation.

Documentation changes appear in the source repository immediately. App-store
README, documentation and changelog files are refreshed with the next real app
publication. A documentation correction alone does not offer an app update or
restart its heartbeat.

## Publication order

1. Revalidate the source, require successful canonical main CI and resolve the
   service publication plan.
2. **For a new app version**, build native images, exercise their
   API/authentication and clean shutdown, then push unique candidate tags.
   There is no wallbox connected in these tests.
3. Match both platform digests to the build runners' trusted local push results
   before attesting the multi-architecture index. OCI labels alone are not
   proof of origin. Verify the exact immutable digest and never replace an
   existing version tag.
4. Attest the new image, verify its provenance and check anonymous pull access.
   Only image publishers have `packages: write` permission. **When reusing the
   app**, verify its existing digest and original provenance; skip image builds
   and publication.
5. Publish and independently download/verify the HACS release in either case.
   Betas are prereleases and never replace the latest stable GitHub release.
6. **For a new app publication**, publish an allowlist of app metadata,
   documentation, artwork and license notices after its image and source
   GitHub release exist. App prereleases update only `apps-beta`; stable app
   releases update both `apps` and `apps-beta` with the same immutable image.
   A catalog cannot move backwards or replace a version. If `apps-beta`
   already offers a newer preview, it stays there while the stable catalog
   advances. **An unchanged app leaves both catalogs untouched.**

The image version is the app version. Its `source_tag` and source commit identify
the integration release that first published it; the catalog release link uses
that original source tag. A later integration release can therefore use the
same service version, digest and provenance.

The app catalog includes `apparmor.txt` for Supervisor to load. It is a relevant
service input: policy changes require the same review, enforcement validation
and new app version as other service changes. The profile is not activated merely
by pulling the standalone OCI image. The [AppArmor check](validation.md#apparmor-enforcement)
loads it only on disposable CI runners; parser-only results do not substitute
for an enforcement run.

The catalog's Dockerfile contains only `FROM image@sha256:<attested-index>`;
its config deliberately has no `image` key. Supervisor's supported local-build
path downloads that immutable image and adds app metadata. It does not build
the service from source or resolve a mutable version tag. The release gate
builds and smoke-tests this same wrapper before publishing. A later GHCR tag
replacement cannot change an app installation; deletion causes installation
to fail instead of silently substituting code. Standalone Docker users get the
same digest reference in the generated catalog README.

When an app publication is needed, the catalog updater validates every target
before changing a branch, then makes
ordinary commits without force or another PR. Both channels share one workflow
concurrency group. After a partial failure, rerunning skips already completed
targets and finishes the remaining promotion. In `release.json`, `channel`
identifies the release's canonical channel and `catalog_channel` identifies the
actual branch; promotion does not rewrite build provenance. Source branches
remain untouched. Keep
catalog URLs stable: Supervisor derives the app identity from the repository
URL including the branch. Existing beta installations receive the stable app
release as a normal update, preserving app data and credentials. A deliberate switch
to the stable-only repository still requires handing over between two app
installations; it is unnecessary for receiving the stable version.

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
Use **Re-run failed jobs** to retain successful native build artifacts. If the
image was already published and attested but catalog publication was interrupted,
the retry can verify and reuse that exact image to finish the catalog update.
Rebuilding can produce different digests; those must not replace an existing
version, even when their source labels match.

Service changes still need hardware acceptance where affected: deliberate
pause, charging-release blocks, HA Core restart, service restart, USB/LAN
recovery and persistent error-200 behavior. CI does not replace that hardware
validation; an independent version does not change this requirement.

The workflow follows Home Assistant's [app publication](https://contributors.home-assistant.io/apps/guides/publishing/)
and [repository/channel](https://contributors.home-assistant.io/apps/guides/repository/)
contracts.
