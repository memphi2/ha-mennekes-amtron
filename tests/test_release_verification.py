"""Published releases must match the source, metadata and downloaded bytes."""

import hashlib
import json
import stat
from zipfile import ZipFile, ZipInfo

import pytest

from scripts.release_check import (
    ASSETS,
    ZIP_NAME,
    verify_archive,
    verify_assets,
    verify_release,
)


def bundle(tmp_path):
    files = {"manifest.json": b'{"version":"0.2.3"}', "LICENSE": b"license", "NOTICE": b"notice"}
    with ZipFile(tmp_path / ZIP_NAME, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    digest = hashlib.sha256((tmp_path / ZIP_NAME).read_bytes()).hexdigest()
    metadata = {"release_tag": "v0.2.3", "repository": "owner/repo", "git_commit": "a" * 40,
                "integration_version": "0.2.3", "zip_entries": len(files), "zip_sha256": digest}
    (tmp_path / "build-metadata.json").write_text(json.dumps(metadata))
    sbom = {"spdxVersion": "SPDX-2.3", "packages": [{"name": "ha-mennekes-amtron",
            "versionInfo": "0.2.3", "licenseDeclared": "Apache-2.0",
            "checksums": [{"algorithm": "SHA256", "checksumValue": digest}]}]}
    (tmp_path / "sbom.spdx.json").write_text(json.dumps(sbom))
    sums = "".join(f"{hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}  {name}\n"
                   for name in sorted(ASSETS - {"SHA256SUMS"}))
    (tmp_path / "SHA256SUMS").write_text(sums)
    release = {"tag_name": "v0.2.3", "draft": False, "prerelease": False, "body": "Release notes",
               "assets": [{"name": name, "size": (tmp_path / name).stat().st_size,
                           "digest": "sha256:" + hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}
                          for name in sorted(ASSETS)]}
    return files, release


def test_verified_download_matches_every_public_contract(tmp_path):
    files, release = bundle(tmp_path)
    verify_release(release, "v0.2.3", "Release notes\n")
    verify_assets(tmp_path, release, "owner/repo", "a" * 40, "0.2.3", files)


@pytest.mark.parametrize("name", sorted(ASSETS))
def test_changed_download_is_rejected(tmp_path, name):
    files, release = bundle(tmp_path)
    (tmp_path / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest"):
        verify_assets(tmp_path, release, "owner/repo", "a" * 40, "0.2.3", files)


@pytest.mark.parametrize("change", ["tag", "notes", "draft", "prerelease", "extra_asset"])
def test_release_metadata_must_match_approved_source(tmp_path, change):
    _, release = bundle(tmp_path)
    if change == "tag":
        release["tag_name"] = "v9.0.0"
    elif change == "notes":
        release["body"] = "other notes"
    elif change == "extra_asset":
        release["assets"].append({"name": "manual.zip"})
    else:
        release[change] = True
    with pytest.raises(ValueError, match=r"release|notes|installation"):
        verify_release(release, "v0.2.3", "Release notes")


@pytest.mark.parametrize("change", ["changed", "extra", "missing", "symlink", "duplicate"])
def test_unsafe_or_wrong_archive_cannot_match_the_tree(tmp_path, change):
    path = tmp_path / "release.zip"
    files = {"a.py": b"original", "LICENSE": b"license"}
    with ZipFile(path, "w") as archive:
        archive.writestr("LICENSE", b"license")
        if change == "symlink":
            info = ZipInfo("a.py")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, files["a.py"])
        elif change != "missing":
            archive.writestr("a.py", b"changed!" if change == "changed" else files["a.py"])
        if change == "extra":
            archive.writestr("../unexpected.py", b"bad")
        if change == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("a.py", files["a.py"])
    with pytest.raises(ValueError, match="archive"):
        verify_archive(path, files)


@pytest.mark.parametrize("target", ["metadata", "sbom", "sums"])
def test_self_consistent_downloads_still_need_correct_release_identity(tmp_path, target):
    files, release = bundle(tmp_path)
    if target == "metadata":
        path = tmp_path / "build-metadata.json"
        data = json.loads(path.read_text())
        data["git_commit"] = "b" * 40
        path.write_text(json.dumps(data))
    elif target == "sbom":
        path = tmp_path / "sbom.spdx.json"
        data = json.loads(path.read_text())
        data["packages"][0]["versionInfo"] = "9.0.0"
        path.write_text(json.dumps(data))
    sums = "".join(f"{hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}  {name}\n"
                   for name in sorted(ASSETS - {"SHA256SUMS"}))
    (tmp_path / "SHA256SUMS").write_text(sums if target != "sums" else sums + sums.splitlines()[0] + "\n")
    for asset in release["assets"]:
        content = (tmp_path / asset["name"]).read_bytes()
        asset.update(size=len(content), digest="sha256:" + hashlib.sha256(content).hexdigest())
    with pytest.raises(ValueError, match=r"metadata|SBOM|checksum"):
        verify_assets(tmp_path, release, "owner/repo", "a" * 40, "0.2.3", files)
