"""Publication must preserve immutable versions and keep unavailable apps hidden."""

from __future__ import annotations

import base64
import json
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
        images, "verify_index", lambda value, info: verified.append((value, info))
    )
    commands = []

    def run(*args):
        commands.append(args)
        assert "create" not in args
        return DIGEST.encode()

    monkeypatch.setattr(images, "run", run)
    assert images.publish(INFO, tmp_path) == DIGEST
    assert verified == [(index, INFO)]
    assert len(commands) == 1


def test_changed_tag_cannot_publish_an_image(monkeypatch, tmp_path):
    candidates(tmp_path)
    monkeypatch.setattr(images, "remote_commit", lambda *_: "d" * 40)
    with pytest.raises(ValueError, match="tag changed"):
        images.publish(INFO, tmp_path)


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
        images.verify_index(index, INFO)


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
    assert {"LICENSE", "NOTICE", "release.json"} <= files.keys()
    assert not any(
        path.endswith((".py", ".zip")) or path.startswith(".github/") for path in files
    )
    assert json.loads(files["release.json"])["digest"] == DIGEST


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


def test_unavailable_image_prevents_all_catalog_mutations(monkeypatch):
    monkeypatch.setattr(catalog, "remote_commit", lambda *_: INFO["commit"])
    monkeypatch.setattr(catalog, "run", lambda *_: DIGEST.encode())

    def unavailable(*_):
        raise ValueError("private image")

    monkeypatch.setattr(catalog, "verify_public", unavailable)
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
    monkeypatch.setattr(catalog, "run", lambda *_: DIGEST.encode())
    monkeypatch.setattr(catalog, "verify_public", lambda *_: None)
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
    for language in ("de", "en"):
        translated = yaml.safe_load(
            (app / "translations" / f"{language}.yaml").read_text()
        )
        assert set(translated["configuration"]) == set(config["schema"])


def test_release_pipeline_orders_image_release_and_catalog_publication():
    workflow = yaml.safe_load(
        (images.ROOT / ".github/workflows/release.yml").read_text()
    )
    jobs = workflow["jobs"]
    assert "main-validation" in jobs["service-images"]["needs"]
    assert "service-manifest" in jobs["publish"]["needs"]
    assert "publish" in jobs["service-catalog"]["needs"]
    assert "service-manifest" in jobs["service-catalog"]["needs"]
    assert jobs["service-catalog"]["concurrency"]["cancel-in-progress"] is False
