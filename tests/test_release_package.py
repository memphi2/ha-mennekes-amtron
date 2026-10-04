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


def _build(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "build_hacs_release.py"),
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return (
        tmp_path / "ha-mennekes-amtron.zip",
        tmp_path / "ha-mennekes-amtron-manual.zip",
    )


def test_the_hacs_zip_has_no_wrapping_directory(tmp_path: Path) -> None:
    """HACS extracts the archive into custom_components/mennekes_amtron."""

    hacs_zip, _manual = _build(tmp_path)
    with zipfile.ZipFile(hacs_zip) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "strings.json" in names
    assert "translations/de.json" in names
    assert "brand/icon.png" in names
    assert not any(name.startswith("custom_components/") for name in names)
    assert not any(name.endswith(".pyc") for name in names)


def test_the_manual_zip_unpacks_onto_a_configuration_directory(
    tmp_path: Path,
) -> None:
    """A human unpacks this one over config/, so it carries the full path."""

    _hacs, manual_zip = _build(tmp_path / "build")
    target = tmp_path / "config"
    target.mkdir()
    with zipfile.ZipFile(manual_zip) as archive:
        archive.extractall(target)
    component = target / "custom_components" / "mennekes_amtron"
    assert (component / "manifest.json").is_file()
    assert (component / "translations" / "de.json").is_file()
    assert (component / "brand" / "icon.png").is_file()


def test_both_archives_carry_the_same_files(tmp_path: Path) -> None:
    hacs_zip, manual_zip = _build(tmp_path)
    with zipfile.ZipFile(hacs_zip) as archive:
        flat = set(archive.namelist())
    with zipfile.ZipFile(manual_zip) as archive:
        nested = {
            name.removeprefix("custom_components/mennekes_amtron/")
            for name in archive.namelist()
        }
    assert flat == nested


def test_only_the_licence_and_notice_travel_with_the_integration(
    tmp_path: Path,
) -> None:
    """Everything else would land in the user's custom_components directory."""

    hacs_zip, _manual = _build(tmp_path)
    with zipfile.ZipFile(hacs_zip) as archive:
        names = set(archive.namelist())
    assert {"LICENSE", "NOTICE"} <= names
    assert not {"PRIVACY.md", "SECURITY.md", "legal.md", "safety.md"} & names


def test_the_build_is_reproducible(tmp_path: Path) -> None:
    first = [path.read_bytes() for path in _build(tmp_path / "a")]
    second = [path.read_bytes() for path in _build(tmp_path / "b")]
    assert first == second


def test_the_release_assets_describe_the_build(tmp_path: Path) -> None:
    zip_path, manual_zip = _build(tmp_path)
    sums = tmp_path / "SHA256SUMS"
    metadata = tmp_path / "build-metadata.json"
    sbom = tmp_path / "sbom.spdx.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "write_release_assets.py"),
            "--zip",
            str(zip_path),
            "--manual-zip",
            str(manual_zip),
            "--tag",
            "v0.1.0",
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
    from manifest_version import read_integration_version

    assert payload["release_tag"] == "v0.1.0"
    assert payload["integration_version"] == read_integration_version()
    assert payload["zip_entries"] > 10
    assert payload["manual_zip_entries"] == payload["zip_entries"]
    assert payload["manual_zip_sha256"] != payload["zip_sha256"]

    document = json.loads(sbom.read_text(encoding="utf-8"))
    assert document["spdxVersion"] == "SPDX-2.3"
    packages = {package["name"] for package in document["packages"]}
    assert "ha-mennekes-amtron" in packages
    assert "pymodbus[serial]" in packages
    versions = {package["name"]: package["versionInfo"] for package in document["packages"]}
    assert versions["pymodbus[serial]"] == ">=3.11.2"
    assert len(document["relationships"]) == 2

    lines = sums.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    assert all(len(line.split("  ")[0]) == 64 for line in lines)


def test_the_integration_version_is_the_single_source(tmp_path: Path) -> None:
    from manifest_version import read_integration_version

    version = read_integration_version()
    versions = json.loads((ROOT / "project-versions.json").read_text(encoding="utf-8"))
    assert versions["integration_version"] == version
    assert f"## {version}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (ROOT / ".github" / "release-notes" / f"v{version}.md").is_file()
