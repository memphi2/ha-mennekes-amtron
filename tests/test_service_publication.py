"""Publication must preserve immutable versions and keep unavailable apps hidden."""

from __future__ import annotations

import ast
import base64
import json
import os
import re
import shutil
import subprocess
from copy import deepcopy

import pytest
import yaml

from scripts import publish_service_catalog as catalog
from scripts import service_image as images
from scripts.build_service_app import export

INFO = {
    "repository": "memphi2/ha-mennekes-amtron",
    "image": "ghcr.io/memphi2/ha-mennekes-amtron-service",
    "version": "0.5.0b1",
    "commit": "a" * 40,
    # The integration's source release deliberately differs from the app version.
    "source_tag": "v0.7.0",
    "channel": "apps-beta",
}
DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64


def candidates(directory):
    for arch, digest in (("amd64", DIGEST), ("aarch64", OTHER_DIGEST)):
        (directory / f"image-{arch}.json").write_text(
            json.dumps({**INFO, "architecture": arch, "digest": digest})
        )


@pytest.mark.parametrize(
    "change",
    ["missing", "extra", "duplicate", "source", "version", "image", "digest", "arch"],
)
def test_candidate_results_must_cover_exactly_the_reviewed_release(tmp_path, change):
    candidates(tmp_path)
    file = tmp_path / "image-amd64.json"
    value = json.loads(file.read_text())
    if change == "missing":
        file.unlink()
    elif change == "extra":
        (tmp_path / "unexpected.json").write_text(json.dumps(value))
    else:
        key, replacement = {
            "duplicate": ("architecture", "aarch64"),
            "source": ("commit", "d" * 40),
            "version": ("version", "0.4.1"),
            "image": ("image", "ghcr.io/other/image"),
            "digest": ("digest", "bad"),
            "arch": ("architecture", "riscv64"),
        }[change]
        value[key] = replacement
        file.write_text(json.dumps(value))
    with pytest.raises(ValueError, match=r"expected|differs"):
        images.validate_candidates(tmp_path, INFO)


def test_publication_rerun_verifies_existing_version_without_replacing_it(
    monkeypatch, tmp_path
):
    candidates(tmp_path)
    monkeypatch.setattr(images, "remote_commit", lambda *_: INFO["commit"])
    index = {"manifests": ["existing"]}
    monkeypatch.setattr(images, "inspect_index", lambda *_, **__: index)
    verified = []
    monkeypatch.setattr(
        images,
        "verify_index",
        lambda value, info, builds: verified.append((value, info, builds)),
    )
    commands = []

    def run(*args):
        commands.append(args)
        assert "create" not in args
        return DIGEST.encode()

    monkeypatch.setattr(images, "run", run)
    assert images.publish(INFO, tmp_path) == DIGEST
    assert verified == [(index, INFO, {"amd64": DIGEST, "aarch64": OTHER_DIGEST})]
    assert len(commands) == 1


def test_changed_tag_cannot_publish_an_image(monkeypatch, tmp_path):
    candidates(tmp_path)
    monkeypatch.setattr(images, "remote_commit", lambda *_: "d" * 40)
    with pytest.raises(ValueError, match="tag changed"):
        images.publish(INFO, tmp_path)


@pytest.mark.parametrize("replaced_after_lookup", [False, True])
def test_existing_image_cannot_gain_provenance_from_copied_source_labels(
    monkeypatch, tmp_path, replaced_after_lookup
):
    candidates(tmp_path)
    monkeypatch.setattr(images, "remote_commit", lambda *_: INFO["commit"])
    good = {
        "manifests": [
            {"digest": DIGEST, "platform": {"os": "linux", "architecture": "amd64"}},
            {
                "digest": OTHER_DIGEST,
                "platform": {"os": "linux", "architecture": "arm64"},
            },
        ]
    }
    forged = deepcopy(good)
    forged["manifests"][0]["digest"] = "sha256:" + "e" * 64
    index_digest = "sha256:" + "f" * 64
    lookups = []

    def inspect(reference, **_kwargs):
        lookups.append(reference)
        if replaced_after_lookup and reference.endswith(":0.5.0b1"):
            return good
        return forged

    def run(*args):
        assert args[:4] == ("docker", "buildx", "imagetools", "inspect")
        # A mutable tag can change after the initial index lookup. Returning
        # this digest for attestation without inspecting it would bless a forgery.
        return index_digest.encode()

    monkeypatch.setattr(images, "inspect_index", inspect)
    monkeypatch.setattr(images, "run", run)
    with pytest.raises(ValueError, match="differs from validated builds"):
        images.publish(INFO, tmp_path)
    assert lookups[-1] == f"{INFO['image']}@{index_digest}"


def test_build_evidence_uses_the_local_push_digest_not_the_remote_tag(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(images, "remote_commit", lambda *_: INFO["commit"])
    monkeypatch.setattr(images, "smoke", lambda *_: None)
    monkeypatch.setattr(
        images.subprocess,
        "run",
        lambda *args, **_kw: subprocess.CompletedProcess(args, 0),
    )
    inspected = []

    def run(*args):
        assert "imagetools" not in args
        if args[:3] == ("docker", "image", "inspect"):
            inspected.append(args[3])
            return json.dumps([f"{INFO['image']}@{DIGEST}"]).encode()
        return b""

    monkeypatch.setattr(images, "run", run)
    output = tmp_path / "image-amd64.json"
    images.build(INFO, "amd64", output)
    assert json.loads(output.read_text())["digest"] == DIGEST
    assert inspected == [f"{INFO['image']}:build-123-1-amd64"]


@pytest.mark.parametrize(
    "error", [b"unauthorized", b"denied", b"network timeout", b"unexpected EOF"]
)
def test_registry_errors_never_mean_a_version_is_absent(monkeypatch, error):
    monkeypatch.setattr(
        images.subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess([], 1, b"", error),
    )
    with pytest.raises(ValueError, match="cannot inspect"):
        images.inspect_index("ghcr.io/example/test:1.0.0", missing_ok=True)


@pytest.mark.parametrize(
    "change", ["platform", "source", "version", "revision", "duplicate"]
)
def test_existing_image_must_match_source_and_both_platforms(monkeypatch, change):
    index = {
        "manifests": [
            {"digest": DIGEST, "platform": {"os": "linux", "architecture": "amd64"}},
            {
                "digest": OTHER_DIGEST,
                "platform": {"os": "linux", "architecture": "arm64"},
            },
        ]
    }
    if change == "duplicate":
        index["manifests"][1] = deepcopy(index["manifests"][0])
    if change == "platform":
        index["manifests"][0]["platform"]["os"] = "windows"

    def run(*args):
        if "pull" in args:
            return b""
        arm = OTHER_DIGEST in args[-1]
        labels = {
            "org.opencontainers.image.source": f"https://github.com/{INFO['repository']}",
            "org.opencontainers.image.revision": INFO["commit"],
            "org.opencontainers.image.version": INFO["version"],
        }
        if change in ("source", "version", "revision"):
            labels[f"org.opencontainers.image.{change}"] = "wrong"
        return json.dumps(
            [
                {
                    "Architecture": "arm64" if arm else "amd64",
                    "Os": "linux",
                    "Config": {"Labels": labels},
                }
            ]
        ).encode()

    monkeypatch.setattr(images, "run", run)
    with pytest.raises(ValueError, match=r"platform|source"):
        images.verify_index(index, INFO, {"amd64": DIGEST, "aarch64": OTHER_DIGEST})


def test_public_access_is_verified_without_build_credentials(monkeypatch):
    monkeypatch.setenv("DOCKER_CONFIG", "/private/build-credentials")

    def run(*args, **kwargs):
        assert kwargs["env"]["DOCKER_CONFIG"] != "/private/build-credentials"
        assert kwargs["env"]["DOCKER_AUTH_CONFIG"] == ""
        return subprocess.CompletedProcess([], 1, b"", b"denied")

    monkeypatch.setattr(images.subprocess, "run", run)
    with pytest.raises(ValueError, match="visibility to public"):
        images.verify_public(INFO, DIGEST)


@pytest.mark.parametrize(
    ("version", "channel"), [("0.5.0b1", "apps-beta"), ("0.5.0", "apps")]
)
def test_catalogs_are_separate_and_allowlist_only_distributable_files(version, channel):
    files = catalog.catalog_files(
        {**INFO, "version": version, "channel": channel}, DIGEST
    )
    assert yaml.safe_load(files["repository.yaml"])["url"].endswith(f"#{channel}")
    assert "mennekes_amtron_modbus/config.yaml" in files
    assert "mennekes_amtron_modbus/DOCS.md" in files
    languages = {
        path.stem
        for path in (images.ROOT / "custom_components/mennekes_amtron_rtu/translations").glob("*.json")
    }
    assert {
        path.removeprefix("mennekes_amtron_modbus/translations/").removesuffix(".yaml")
        for path in files
        if path.startswith("mennekes_amtron_modbus/translations/")
    } == languages
    for language in languages:
        assert files[f"mennekes_amtron_modbus/translations/{language}.yaml"] == (
            images.ROOT / "modbus_service/translations" / f"{language}.yaml"
        ).read_bytes()
    assert {"LICENSE", "NOTICE", "release.json"} <= files.keys()
    assert not any(
        path.endswith((".py", ".zip")) or path.startswith(".github/") for path in files
    )
    assert json.loads(files["release.json"])["digest"] == DIGEST
    assert json.loads(files["release.json"])["catalog_channel"] == channel
    config = yaml.safe_load(files["mennekes_amtron_modbus/config.yaml"])
    assert "image" not in config  # Supervisor must select the build path.
    assert files["mennekes_amtron_modbus/Dockerfile"] == (
        f"FROM {INFO['image']}@{DIGEST}\n".encode()
    )
    assert f"{INFO['image']}@{DIGEST}".encode() in files["README.md"]
    assert f"/releases/tag/{INFO['source_tag']}".encode() in files["README.md"]


@pytest.mark.parametrize("digest", ["latest", "sha256:short", DIGEST + "\nRUN evil"])
def test_catalog_rejects_unpinned_or_injected_image_references(digest):
    with pytest.raises(ValueError, match="image or digest"):
        catalog.catalog_files(INFO, digest)


def test_catalog_rejects_a_different_registry_image():
    with pytest.raises(ValueError, match="image or digest"):
        catalog.catalog_files({**INFO, "image": "ghcr.io/other/image"}, DIGEST)


def test_release_smokes_the_digest_bound_supervisor_build(monkeypatch):
    from pathlib import Path

    def run(*args):
        assert args[:5] == ("docker", "build", "--pull", "--tag", "amtron-app:verify")
        assert (Path(args[5]) / "Dockerfile").read_bytes() == (
            catalog.catalog_files(INFO, DIGEST)["mennekes_amtron_modbus/Dockerfile"]
        )
        return b""

    exercised = []
    monkeypatch.setattr(images, "run", run)
    monkeypatch.setattr(images, "smoke", exercised.append)
    images.verify_app_install(INFO, DIGEST)
    assert exercised == ["amtron-app:verify"]


@pytest.mark.parametrize("change", ["downgrade", "digest", "source", "channel"])
def test_catalog_never_regresses_or_replaces_a_release(change):
    old = {**INFO, "digest": DIGEST}
    if change == "downgrade":
        old["version"] = "0.5.0b2"
    elif change == "digest":
        old["digest"] = OTHER_DIGEST
    elif change == "source":
        old["commit"] = "d" * 40
    else:
        old["channel"] = "apps"
    with pytest.raises(ValueError, match=r"downgrade|differs|replacement"):
        catalog.check_forward(old, INFO, DIGEST)


def test_catalog_same_version_is_idempotent_and_newer_version_can_advance():
    assert catalog.check_forward({**INFO, "digest": DIGEST}, INFO, DIGEST) is False
    assert (
        catalog.check_forward(
            {**INFO, "version": "0.5.0b1", "digest": DIGEST},
            {**INFO, "version": "0.5.0b2"},
            OTHER_DIGEST,
        )
        is True
    )


def test_stable_promotion_keeps_provenance_and_catalog_identity_separate():
    info = {**INFO, "version": "0.5.0", "channel": "apps"}
    stable = catalog.catalog_files(info, DIGEST, branch="apps")
    preview = catalog.catalog_files(info, DIGEST, branch="apps-beta")
    assert stable["mennekes_amtron_modbus/Dockerfile"] == preview["mennekes_amtron_modbus/Dockerfile"]
    assert stable["mennekes_amtron_modbus/config.yaml"] == preview["mennekes_amtron_modbus/config.yaml"]
    assert yaml.safe_load(preview["repository.yaml"])["url"].endswith("#apps-beta")
    metadata = json.loads(preview["release.json"])
    assert metadata == {**info, "digest": DIGEST, "catalog_channel": "apps-beta"}
    assert catalog.check_forward({**INFO, "digest": DIGEST}, info, DIGEST, branch="apps-beta")
    assert not catalog.check_forward(metadata, info, DIGEST, branch="apps-beta")
    assert catalog.check_forward(metadata, {**INFO, "version": "0.6.0b1"}, OTHER_DIGEST)


@pytest.mark.parametrize("version", ["0.5.0b1", "0.5.0rc1"])
def test_prereleases_cannot_be_rendered_into_the_stable_catalog(version):
    with pytest.raises(ValueError, match="not allowed"):
        catalog.catalog_files({**INFO, "version": version}, DIGEST, branch="apps")


def catalog_repository(monkeypatch, initial):
    """Simulate the Git API, including immutable commits and failed ref updates."""
    references, commits, trees = {}, {}, {}
    events, published, fail_once = [], [], set()
    serial = 0

    def new_sha():
        nonlocal serial
        serial += 1
        return f"{serial:040x}"

    for branch, metadata in initial.items():
        sha = new_sha()
        references[branch] = sha
        commits[sha] = deepcopy(metadata)

    def api(_repository, endpoint, payload=None, **_kwargs):
        events.append((endpoint, payload))
        if endpoint.startswith("git/ref/heads/"):
            sha = references.get(endpoint.removeprefix("git/ref/heads/"))
            return {"object": {"sha": sha}} if sha is not None else None
        if endpoint.startswith("contents/release.json?ref="):
            metadata = commits[endpoint.split("=", 1)[1]]
            return {"content": base64.b64encode(json.dumps(metadata).encode()).decode()}
        if endpoint == "git/trees":
            sha = new_sha()
            trees[sha] = json.loads(next(
                item["content"] for item in payload["tree"] if item["path"] == "release.json"
            ))
            return {"sha": sha}
        if endpoint == "git/commits":
            sha = new_sha()
            commits[sha] = trees[payload["tree"]]
            return {"sha": sha}
        if endpoint == "git/refs":
            branch = payload["ref"].removeprefix("refs/heads/")
            assert branch not in references
            references[branch] = payload["sha"]
            published.append(branch)
            return {"object": {"sha": payload["sha"]}}
        assert endpoint == "git/blobs"
        return {"sha": new_sha()}

    def patch(command, **kwargs):
        assert command[3] == "PATCH"
        branch = command[4].rsplit("/", 1)[1]
        payload = json.loads(kwargs["input"])
        assert payload["force"] is False
        if branch in fail_once:
            fail_once.remove(branch)
            return subprocess.CompletedProcess(command, 1, b"", b"interrupted")
        references[branch] = payload["sha"]
        published.append(branch)
        return subprocess.CompletedProcess(command, 0, b"{}", b"")

    monkeypatch.setattr(catalog, "remote_commit", lambda *_: INFO["commit"])
    def verified_image(_info):
        events.append(("verified", None))
        return DIGEST

    monkeypatch.setattr(catalog, "existing_verified_image", verified_image)
    monkeypatch.setattr(catalog, "api", api)
    monkeypatch.setattr(catalog.subprocess, "run", patch)
    return references, commits, events, published, fail_once


def test_stable_publication_updates_both_catalogs_from_the_same_verified_image(monkeypatch):
    refs, commits, events, published, _fail = catalog_repository(monkeypatch, {})
    info = {**INFO, "version": "0.5.0", "channel": "apps"}
    catalog.publish(info, DIGEST)
    assert published == ["apps", "apps-beta"]
    assert events[0] == ("verified", None)
    for branch in published:
        assert commits[refs[branch]] == {**info, "digest": DIGEST, "catalog_channel": branch}
    # Both references were read before creating even the first blob or tree.
    assert [endpoint for endpoint, _ in events[1:3]] == [
        "git/ref/heads/apps", "git/ref/heads/apps-beta"
    ]


def test_beta_publication_never_accesses_the_stable_catalog(monkeypatch):
    initial = {**INFO, "version": "0.4.1", "channel": "apps", "digest": OTHER_DIGEST}
    refs, commits, events, published, _fail = catalog_repository(monkeypatch, {"apps": initial})
    old_stable = refs["apps"]
    catalog.publish(INFO, DIGEST)
    assert published == ["apps-beta"]
    assert refs["apps"] == old_stable
    assert commits[old_stable] == initial
    assert "git/ref/heads/apps" not in [endpoint for endpoint, _ in events]


def test_partial_promotion_can_retry_without_republishing_the_completed_catalog(monkeypatch):
    initial = {
        "apps": {**INFO, "version": "0.4.1", "channel": "apps", "digest": OTHER_DIGEST},
        "apps-beta": {**INFO, "digest": OTHER_DIGEST},
    }
    refs, commits, _events, published, fail = catalog_repository(monkeypatch, initial)
    fail.add("apps-beta")
    info = {**INFO, "version": "0.5.0", "channel": "apps"}
    with pytest.raises(ValueError, match="concurrently"):
        catalog.publish(info, DIGEST)
    completed = refs["apps"]
    assert published == ["apps"]
    catalog.publish(info, DIGEST)
    assert refs["apps"] == completed
    assert published == ["apps", "apps-beta"]
    assert commits[refs["apps-beta"]] == {**info, "digest": DIGEST, "catalog_channel": "apps-beta"}
    catalog.publish(info, DIGEST)
    assert published == ["apps", "apps-beta"]


def test_stable_maintenance_update_keeps_a_newer_preview_catalog(monkeypatch):
    initial = {"apps-beta": {**INFO, "version": "0.6.0b1", "digest": OTHER_DIGEST}}
    refs, commits, _events, published, _fail = catalog_repository(monkeypatch, initial)
    preview = refs["apps-beta"]
    catalog.publish({**INFO, "version": "0.5.0", "channel": "apps"}, DIGEST)
    assert published == ["apps"]
    assert refs["apps-beta"] == preview
    assert commits[preview] == initial["apps-beta"]


@pytest.mark.parametrize("failure", ["downgrade", "source", "channel"])
def test_invalid_catalog_preflight_prevents_every_target_mutation(monkeypatch, failure):
    info = {**INFO, "version": "0.5.0", "channel": "apps"}
    if failure == "downgrade":
        initial = {"apps": {**info, "version": "0.5.1", "digest": OTHER_DIGEST}}
    else:
        preview = {**info, "digest": DIGEST, "catalog_channel": "apps-beta"}
        preview["commit" if failure == "source" else "catalog_channel"] = "different"
        initial = {"apps-beta": preview}
    refs, _commits, events, published, _fail = catalog_repository(monkeypatch, initial)
    old_refs = dict(refs)
    with pytest.raises(ValueError, match=r"downgrade|differs"):
        catalog.publish(info, DIGEST)
    assert not published
    assert refs == old_refs
    assert all(payload is None for _, payload in events)


def test_unavailable_image_prevents_all_catalog_mutations(monkeypatch):
    monkeypatch.setattr(catalog, "remote_commit", lambda *_: INFO["commit"])

    def unavailable(*_):
        raise ValueError("private image")

    monkeypatch.setattr(catalog, "existing_verified_image", unavailable)
    monkeypatch.setattr(
        catalog,
        "api",
        lambda *_args, **_kwargs: pytest.fail(
            "catalog accessed before public image verification"
        ),
    )
    with pytest.raises(ValueError, match="private image"):
        catalog.publish(INFO, DIGEST)


def test_catalog_updates_use_fast_forward_only(monkeypatch):
    monkeypatch.setattr(catalog, "remote_commit", lambda *_: INFO["commit"])
    monkeypatch.setattr(catalog, "existing_verified_image", lambda *_: DIGEST)
    previous = {**INFO, "version": "0.4.0b1", "digest": OTHER_DIGEST}

    def api(_repository, endpoint, payload=None, **_kwargs):
        if endpoint.startswith("git/ref/"):
            return {"object": {"sha": "e" * 40}}
        if endpoint.startswith("contents/"):
            return {"content": base64.b64encode(json.dumps(previous).encode()).decode()}
        return {"sha": "f" * 40}

    monkeypatch.setattr(catalog, "api", api)

    def run(*_args, **kwargs):
        assert json.loads(kwargs["input"])["force"] is False
        return subprocess.CompletedProcess([], 1, b"", b"not fast forward")

    monkeypatch.setattr(catalog.subprocess, "run", run)
    with pytest.raises(ValueError, match="concurrently"):
        catalog.publish(INFO, DIGEST)


def test_local_export_builds_the_checkout_instead_of_pulling_a_published_image(
    tmp_path,
):
    output = tmp_path / "app"
    export(output)
    assert "image" not in yaml.safe_load((output / "config.yaml").read_text())
    assert (output / "Dockerfile").is_file()
    with pytest.raises(FileExistsError):
        export(output)


def test_app_translations_cover_every_configuration_option():
    app = images.ROOT / "modbus_service"
    config = yaml.safe_load((app / "config.yaml").read_text())
    for path in (app / "translations").glob("*.yaml"):
        translated = yaml.safe_load(path.read_text())
        assert set(translated["configuration"]) == set(config["schema"])


def test_release_pipeline_orders_image_release_and_catalog_publication():
    workflow = yaml.safe_load(
        (images.ROOT / ".github/workflows/release.yml").read_text()
    )
    jobs = workflow["jobs"]
    assert "main-validation" in jobs["service-images"]["needs"]
    assert "service-plan" in jobs["service-images"]["needs"]
    assert set(jobs["service-plan"]["needs"]) == {"validate", "hacs", "hassfest", "main-validation"}
    assert "service-manifest" in jobs["publish"]["needs"]
    assert "publish" in jobs["service-catalog"]["needs"]
    assert "service-manifest" in jobs["service-catalog"]["needs"]
    assert jobs["service-catalog"]["concurrency"]["cancel-in-progress"] is False
    assert jobs["service-catalog"]["concurrency"]["group"] == "service-catalog"


@pytest.mark.parametrize("expected", ["matching", "changed", "missing", "invalid"])
def test_release_planner_checks_trusted_main_before_running_repository_code(tmp_path, expected):
    workflow = yaml.safe_load((images.ROOT / ".github/workflows/release.yml").read_text())
    steps = workflow["jobs"]["service-plan"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[0]["with"]["ref"] == "refs/heads/main"
    assert steps[0]["with"]["persist-credentials"] is False
    guard = steps[1]
    assert guard["env"] == {"VALIDATED_COMMIT": "${{ needs.main-validation.outputs.commit }}"}
    assert "run" in guard
    assert steps[2]["uses"].startswith("actions/setup-python@")
    git_path = shutil.which("git")
    bash_path = shutil.which("bash")
    assert git_path
    assert bash_path

    def git(*args):
        return subprocess.run(
            [git_path, "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "--quiet")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "--allow-empty", "-m", "validated")
    validated = git("rev-parse", "HEAD")
    if expected == "changed":
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "--allow-empty", "-m", "advanced")
    env = dict(os.environ)
    env.pop("VALIDATED_COMMIT", None)
    if expected != "missing":
        env["VALIDATED_COMMIT"] = "$(touch injected)" if expected == "invalid" else validated
    result = subprocess.run(
        [bash_path, "-c", guard["run"]], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) is (expected == "matching")
    assert not (tmp_path / "injected").exists()


def test_release_buildx_never_restores_or_populates_a_shared_binary_cache():
    workflow = yaml.safe_load((images.ROOT / ".github/workflows/release.yml").read_text())
    buildx_jobs = set()
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            if step.get("uses", "").startswith("docker/setup-buildx-action@"):
                buildx_jobs.add(name)
                assert step["with"]["cache-binary"] is False
    assert buildx_jobs == {"service-plan", "service-images", "service-manifest", "service-catalog"}


def test_app_metadata_and_source_release_versions_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "ROOT", tmp_path)
    app = tmp_path / "modbus_service"
    app.mkdir()
    (app / "config.yaml").write_text(yaml.safe_dump({"arch": ["amd64", "aarch64"]}))
    monkeypatch.setattr(images, "read_integration_version", lambda: "0.9.3")
    monkeypatch.setattr(images, "read_service_version", lambda root: "0.6.2" if root == tmp_path else None)
    monkeypatch.setattr(images, "run", lambda *_: INFO["commit"].encode())
    info = images.release_info(INFO["repository"], "v0.9.3")
    assert info["version"] == "0.6.2"
    assert info["source_tag"] == "v0.9.3"
    assert info["channel"] == "apps"
    with pytest.raises(ValueError, match="tag differs"):
        images.release_info(INFO["repository"], "v0.6.2")


def test_missing_service_image_is_the_only_rebuild_case(monkeypatch):
    checked = []
    monkeypatch.setattr(images, "remote_commit", lambda repo, tag: checked.append((repo, tag)) or INFO["commit"])
    monkeypatch.setattr(images, "inspect_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(images, "run", lambda *_: pytest.fail("no existing image to verify"))
    assert images.existing_verified_image(INFO) is None
    assert checked == [(INFO["repository"], INFO["source_tag"])]


@pytest.mark.parametrize("failure", [None, "source_tag", "attestation", "public", "digest"])
def test_reused_image_requires_its_original_source_attestation_and_public_access(monkeypatch, failure):
    monkeypatch.setattr(images, "remote_commit", lambda *_: "bad" if failure == "source_tag" else INFO["commit"])
    monkeypatch.setattr(images, "inspect_index", lambda *_args, **_kwargs: {"manifests": []})
    commands = []
    public = []

    def run(*args):
        commands.append(args)
        if args[0] == "docker":
            assert args[4] == f"{INFO['image']}:{INFO['version']}"
            return b"not-a-digest" if failure == "digest" else DIGEST.encode()
        assert args == (
            "gh", "attestation", "verify", f"oci://{INFO['image']}@{DIGEST}",
            "--repo", INFO["repository"],
            "--signer-workflow", f"{INFO['repository']}/.github/workflows/release.yml",
            "--source-ref", f"refs/tags/{INFO['source_tag']}",
            "--source-digest", INFO["commit"],
        )
        if failure == "attestation":
            raise subprocess.CalledProcessError(1, args)
        return b"verified"

    def verify_public(info, digest):
        public.append((info, digest))
        if failure == "public":
            raise ValueError("private image")

    monkeypatch.setattr(images, "run", run)
    monkeypatch.setattr(images, "verify_public", verify_public)
    if failure is None:
        assert images.existing_verified_image(INFO) == DIGEST
        assert len(commands) == 2
        assert public == [(INFO, DIGEST)]
    else:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            images.existing_verified_image(INFO)
        if failure != "public":
            assert not public


def test_legacy_catalog_without_source_tag_retains_original_release_identity():
    info = {**INFO, "source_tag": f"v{INFO['version']}"}
    old = {key: value for key, value in info.items() if key != "source_tag"}
    assert catalog.check_forward({**old, "digest": DIGEST}, info, DIGEST) is False
    with pytest.raises(ValueError, match="replacement"):
        catalog.check_forward({**old, "digest": DIGEST}, INFO, DIGEST)


@pytest.mark.parametrize("actual", [None, OTHER_DIGEST, DIGEST])
def test_resume_cli_requires_the_digest_approved_by_the_plan(monkeypatch, capsys, actual):
    monkeypatch.setattr(images, "release_info", lambda *_: dict(INFO))
    monkeypatch.setattr(images, "existing_verified_image", lambda *_: actual)
    monkeypatch.setattr("sys.argv", [
        "service_image.py", "existing", "--tag", INFO["source_tag"], "--digest", DIGEST,
    ])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert images.main() == (0 if actual == DIGEST else 1)
    result = capsys.readouterr()
    if actual == DIGEST:
        assert json.loads(result.out) == {**INFO, "digest": DIGEST}
    else:
        assert not result.out
        assert "missing" in result.err or "differs from the release plan" in result.err


def _job_runs(jobs, name, results, *, build, publish):
    """Evaluate the actual workflow's small boolean condition grammar."""
    job = jobs[name]
    expression = job.get("if", "true").strip().removeprefix("${{").removesuffix("}}").strip()
    dependencies = job.get("needs", [])
    if "always()" not in expression and any(results.get(key, "success") != "success" for key in dependencies):
        return False
    values = {"build_required": build, "catalog_required": publish}
    expression = re.sub(
        r"needs\.([\w-]+)\.(result|outputs\.\w+)",
        lambda match: repr(results.get(match[1], "success") if match[2] == "result" else values[match[2].split(".")[1]]),
        expression,
    )
    expression = expression.replace("always()", "True").replace("&&", " and ").replace("||", " or ")

    def evaluate(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [evaluate(value) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return evaluate(node.left) == evaluate(node.comparators[0])
        raise AssertionError(f"unsupported workflow expression: {ast.dump(node)}")

    return evaluate(ast.parse(expression.replace("\n", " "), mode="eval").body)


@pytest.mark.parametrize(("build", "publish", "image_result", "manifest_result"), [
    ("false", "false", "skipped", "skipped"),
    ("true", "true", "success", "success"),
    ("false", "true", "skipped", "success"),
])
def test_release_workflow_allows_reuse_fresh_build_and_partial_publication_resume(
    build, publish, image_result, manifest_result
):
    jobs = yaml.safe_load((images.ROOT / ".github/workflows/release.yml").read_text())["jobs"]
    results = {"service-images": image_result, "service-manifest": manifest_result}
    assert _job_runs(jobs, "service-images", results, build=build, publish=publish) is (build == "true")
    assert _job_runs(jobs, "service-manifest", results, build=build, publish=publish) is (publish == "true")
    assert _job_runs(jobs, "publish", results, build=build, publish=publish)
    assert _job_runs(jobs, "service-catalog", results, build=build, publish=publish) is (publish == "true")


@pytest.mark.parametrize("gate", ["validate", "hacs", "hassfest", "main-validation", "service-plan", "service-manifest"])
@pytest.mark.parametrize("failure", ["failure", "cancelled", "skipped"])
def test_release_workflow_never_ignores_a_required_gate_failure(gate, failure):
    jobs = yaml.safe_load((images.ROOT / ".github/workflows/release.yml").read_text())["jobs"]
    assert not _job_runs(jobs, "publish", {gate: failure}, build="true", publish="true")


@pytest.mark.parametrize("failure", ["failure", "cancelled", "skipped"])
def test_app_image_failure_blocks_manifest_even_with_always(failure):
    jobs = yaml.safe_load((images.ROOT / ".github/workflows/release.yml").read_text())["jobs"]
    assert not _job_runs(jobs, "service-manifest", {"service-images": failure}, build="true", publish="true")
