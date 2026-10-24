"""Meta tests: the release artifacts have to be buildable and complete."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _build(tmp_path: Path) -> Path:
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
    return tmp_path / "ha-mennekes-amtron.zip"


def test_the_hacs_zip_has_no_wrapping_directory(tmp_path: Path) -> None:
    """HACS extracts the archive into custom_components/mennekes_amtron_rtu."""

    hacs_zip = _build(tmp_path)
    with zipfile.ZipFile(hacs_zip) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["domain"] == "mennekes_amtron_rtu"
    assert "manifest.json" in names
    assert "strings.json" in names
    assert "translations/de.json" in names
    assert "brand/icon.png" in names
    assert not any(name.startswith("custom_components/") for name in names)
    assert not any(name.endswith(".pyc") for name in names)


def test_installing_the_hacs_zip_keeps_an_existing_tcp_component(
    tmp_path: Path,
) -> None:
    """HACS extracts into the RTU component directory, alongside TCP."""

    hacs_zip = _build(tmp_path / "build")
    target = tmp_path / "config"
    target.mkdir()
    # A TCP integration using the original domain must remain untouched.
    tcp = target / "custom_components" / "mennekes_amtron"
    tcp.mkdir(parents=True)
    sentinel = tcp / "manifest.json"
    sentinel.write_text('{"domain": "mennekes_amtron", "name": "TCP"}')
    component = target / "custom_components" / "mennekes_amtron_rtu"
    with zipfile.ZipFile(hacs_zip) as archive:
        archive.extractall(component)
    assert sentinel.read_text() == '{"domain": "mennekes_amtron", "name": "TCP"}'
    assert list(tcp.iterdir()) == [sentinel]
    assert (component / "manifest.json").is_file()
    assert (component / "translations" / "de.json").is_file()
    assert (component / "brand" / "icon.png").is_file()


def test_the_build_produces_only_the_hacs_zip(tmp_path: Path) -> None:
    archive = _build(tmp_path)
    assert list(tmp_path.glob("*.zip")) == [archive]


def test_only_the_licence_and_notice_travel_with_the_integration(
    tmp_path: Path,
) -> None:
    """Everything else would land in the user's custom_components directory."""

    hacs_zip = _build(tmp_path)
    with zipfile.ZipFile(hacs_zip) as archive:
        names = set(archive.namelist())
    assert {"LICENSE", "NOTICE"} <= names
    assert not {"PRIVACY.md", "SECURITY.md", "legal.md", "safety.md"} & names


def test_the_build_is_reproducible(tmp_path: Path) -> None:
    first = _build(tmp_path / "a").read_bytes()
    second = _build(tmp_path / "b").read_bytes()
    assert first == second


def test_the_release_assets_describe_the_build(tmp_path: Path) -> None:
    from manifest_version import read_integration_version

    version = read_integration_version()
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
            f"v{version}",
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
    assert payload["release_tag"] == f"v{version}"
    assert payload["integration_version"] == version
    assert payload["zip_entries"] > 10
    assert payload["zip_sha256"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()

    document = json.loads(sbom.read_text(encoding="utf-8"))
    assert document["spdxVersion"] == "SPDX-2.3"
    packages = {package["name"] for package in document["packages"]}
    assert "ha-mennekes-amtron" in packages
    assert "pymodbus[serial]" in packages
    versions = {package["name"]: package["versionInfo"] for package in document["packages"]}
    assert versions["pymodbus[serial]"] == ">=3.13.1"
    assert len(document["relationships"]) == 2

    lines = sums.read_text(encoding="utf-8").strip().splitlines()
    entries = dict(line.split("  ")[::-1] for line in lines)
    assert set(entries) == {zip_path.name, metadata.name, sbom.name}
    for name, digest in entries.items():
        assert digest == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()


def test_the_integration_version_is_the_single_source(tmp_path: Path) -> None:
    from manifest_version import read_integration_version

    version = read_integration_version()
    versions = json.loads((ROOT / "project-versions.json").read_text(encoding="utf-8"))
    assert versions["integration_version"] == version
    assert f"## {version}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (ROOT / ".github" / "release-notes" / f"v{version}.md").is_file()
