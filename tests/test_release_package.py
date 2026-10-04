"""Meta tests: the release artifacts have to be buildable and complete."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _build(tmp_path: Path) -> Path:
    output = tmp_path / "ha-mennekes-amtron.zip"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_hacs_release.py"), "--output", str(output)],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return output


def test_the_zip_carries_the_integration_and_its_legal_metadata(
    tmp_path: Path,
) -> None:
    with zipfile.ZipFile(_build(tmp_path)) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "strings.json" in names
    assert "translations/de.json" in names
    assert "brand/icon.png" in names
    assert {"LICENSE", "NOTICE", "PRIVACY.md", "SECURITY.md"} <= names
    assert "legal.md" in names
    assert "safety.md" in names
    assert not any(name.endswith(".pyc") for name in names)


def test_the_build_is_reproducible(tmp_path: Path) -> None:
    first = _build(tmp_path / "a").read_bytes()
    second = _build(tmp_path / "b").read_bytes()
    assert first == second


def test_the_release_assets_describe_the_build(tmp_path: Path) -> None:
    zip_path = _build(tmp_path)
    sums = tmp_path / "SHA256SUMS"
    metadata = tmp_path / "build-metadata.json"
    sbom = tmp_path / "sbom.spdx.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "write_release_assets.py"),
            "--zip",
            str(zip_path),
            "--tag",
            "v0.1",
            "--repository",
            "example/ha-mennekes-amtron",
            "--sha256sums",
            str(sums),
            "--metadata",
            str(metadata),
            "--sbom",
            str(sbom),
        ],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["release_tag"] == "v0.1"
    assert payload["integration_version"] == "0.1"
    assert payload["zip_entries"] > 10

    document = json.loads(sbom.read_text(encoding="utf-8"))
    assert document["spdxVersion"] == "SPDX-2.3"
    packages = {package["name"] for package in document["packages"]}
    assert "ha-mennekes-amtron" in packages
    assert "pymodbus[serial]" in packages
    assert len(document["relationships"]) == 2

    lines = sums.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert all(len(line.split("  ")[0]) == 64 for line in lines)


def test_the_integration_version_is_the_single_source(tmp_path: Path) -> None:
    from manifest_version import read_integration_version

    version = read_integration_version()
    versions = json.loads((ROOT / "project-versions.json").read_text(encoding="utf-8"))
    assert versions["integration_version"] == version
    assert f"## {version}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (ROOT / ".github" / "release-notes" / f"v{version}.md").is_file()
