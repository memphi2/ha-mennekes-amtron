"""Publication must be verified, repeatable and unable to overwrite assets."""

import importlib
import json
import shutil
from pathlib import Path

import pytest

from tests.test_release_verification import ASSETS, bundle


def setup_publication(
    monkeypatch,
    tmp_path,
    *,
    existing=False,
    draft=False,
    corrupt=False,
    version="0.2.3",
):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    publisher = importlib.import_module("publish_release")
    local = tmp_path / "local"
    local.mkdir()
    files, release = bundle(local, version)
    release["draft"] = draft
    remote = [release] if existing else []
    calls = []

    def run(*args):
        calls.append(args)
        if args[:2] == ("git", "rev-parse"):
            return b"a" * 40
        if args[:2] == ("git", "show"):
            return b"Release notes"
        if args[:2] == ("gh", "api"):
            return json.dumps([remote]).encode()
        if args[:3] == ("gh", "release", "create"):
            release["draft"] = True
            remote.append(release)
        elif args[:3] == ("gh", "release", "download"):
            destination = Path(args[args.index("--dir") + 1])
            for name in ASSETS:
                shutil.copyfile(local / name, destination / name)
            if corrupt:
                (destination / "SHA256SUMS").write_text("corrupt")
        elif args[:3] == ("gh", "release", "edit"):
            assert "--draft=false" in args
            release["draft"] = False
        else:
            pytest.fail(f"Unexpected command: {args}")
        return b""

    monkeypatch.setattr(publisher, "run", run)
    monkeypatch.setattr(publisher, "remote_commit", lambda *_: "a" * 40)
    monkeypatch.setattr(publisher, "source_files", lambda _: files)
    return publisher, local, release, calls


@pytest.mark.parametrize("version", ["0.5.0b1", "0.5.0rc1"])
def test_prerelease_never_becomes_latest_and_retains_asset_verification(
    monkeypatch, tmp_path, version
):
    publisher, local, release, calls = setup_publication(
        monkeypatch, tmp_path, version=version
    )
    publisher.publish("owner/repo", f"v{version}", local)
    assert release["prerelease"] is True
    assert release["draft"] is False
    create = next(call for call in calls if call[:3] == ("gh", "release", "create"))
    edit = next(call for call in calls if call[:3] == ("gh", "release", "edit"))
    assert "--prerelease" in create
    assert "--latest=false" in create
    assert "--latest=false" in edit


@pytest.mark.parametrize(
    ("existing", "draft"), [(False, False), (True, True), (True, False)]
)
def test_publication_verifies_download_before_visibility_and_never_replaces(
    monkeypatch, tmp_path, existing, draft
):
    publisher, local, release, calls = setup_publication(
        monkeypatch, tmp_path, existing=existing, draft=draft
    )
    publisher.publish("owner/repo", "v0.2.3", local)
    assert not release["draft"]
    assert not any("upload" in call or "--clobber" in call for call in calls)
    downloads = [
        i for i, call in enumerate(calls) if call[:3] == ("gh", "release", "download")
    ]
    edits = [i for i, call in enumerate(calls) if call[:3] == ("gh", "release", "edit")]
    assert downloads
    assert bool(edits) == (not existing or draft)
    if edits:
        assert downloads[-1] < edits[0]
    before = len(calls)
    publisher.publish("owner/repo", "v0.2.3", local)
    assert not any(
        call[:3] in (("gh", "release", "create"), ("gh", "release", "edit"))
        for call in calls[before:]
    )


def test_corrupt_download_leaves_draft_unpublished(monkeypatch, tmp_path):
    publisher, local, release, calls = setup_publication(
        monkeypatch, tmp_path, corrupt=True
    )
    with pytest.raises(ValueError, match="digest"):
        publisher.publish("owner/repo", "v0.2.3", local)
    assert release["draft"]
    assert not any(call[:3] == ("gh", "release", "edit") for call in calls)


def test_api_failure_does_not_mean_release_absent(monkeypatch, tmp_path):
    publisher, _local, _release, _calls = setup_publication(monkeypatch, tmp_path)

    def fail(*_args):
        raise ValueError("API unavailable")

    monkeypatch.setattr(publisher, "run", fail)
    with pytest.raises(ValueError, match="API unavailable"):
        publisher.find_release("owner/repo", "v0.2.3")


def test_release_metadata_and_checksums_are_reproducible(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    assets = importlib.import_module("write_release_assets")
    bundle(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "write_release_assets.py",
            "--tag",
            "v0.3.0",
            "--repository",
            "owner/repo",
            "--zip",
            str(tmp_path / "ha-mennekes-amtron.zip"),
            "--metadata",
            str(tmp_path / "build-metadata.json"),
            "--sbom",
            str(tmp_path / "sbom.spdx.json"),
            "--sha256sums",
            str(tmp_path / "SHA256SUMS"),
        ],
    )
    assert assets.main() == 0
    first = {name: (tmp_path / name).read_bytes() for name in ASSETS}
    assert assets.main() == 0
    assert first == {name: (tmp_path / name).read_bytes() for name in ASSETS}


def test_changed_but_self_consistent_public_assets_are_not_replaced(
    monkeypatch, tmp_path
):
    import hashlib

    publisher, local, _release, calls = setup_publication(
        monkeypatch, tmp_path, existing=True
    )
    original_run = publisher.run

    def run(*args):
        result = original_run(*args)
        if args[:2] == ("gh", "api"):
            pages = json.loads(result)
            for asset in pages[0][0]["assets"]:
                if asset["name"] == "SHA256SUMS":
                    content = (
                        b"\n".join(
                            reversed((local / "SHA256SUMS").read_bytes().splitlines())
                        )
                        + b"\n"
                    )
                    asset["size"] = len(content)
                    asset["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()
            return json.dumps(pages).encode()
        if args[:3] == ("gh", "release", "download"):
            path = Path(args[args.index("--dir") + 1]) / "SHA256SUMS"
            path.write_bytes(
                b"\n".join(reversed(path.read_bytes().splitlines())) + b"\n"
            )
        return result

    monkeypatch.setattr(publisher, "run", run)
    with pytest.raises(ValueError, match="existing assets differ"):
        publisher.publish("owner/repo", "v0.2.3", local)
    assert not any(
        call[:3]
        in (
            ("gh", "release", "create"),
            ("gh", "release", "edit"),
            ("gh", "release", "upload"),
        )
        for call in calls
    )
