"""Meta tests: the repository must not ship identifying or secret values."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import check_repo
import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "mennekes_amtron_rtu"
IGNORED = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".release",
    "__pycache__",
    "htmlcov",
}
# The files that define or exercise the patterns necessarily contain them.
PATTERN_SOURCES = {
    "scripts/check_repo.py",
    "tests/test_privacy_security.py",
    "tests/test_flow_serial.py",
}
SECRETS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ssh-rsa AAAA"),
    re.compile(r"/dev/serial/by-id/usb-[A-Za-z0-9_.-]{12,}"),
    re.compile(r"\bpassword\s*=\s*[\"'][^\"']+[\"']"),
)


def _tracked_text_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix in check_repo.TEXT_SUFFIXES
        and not IGNORED.intersection(path.relative_to(ROOT).parts)
    ]


def test_no_tracked_file_carries_a_secret_or_identifying_path() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _tracked_text_files()
        if str(path.relative_to(ROOT)) not in PATTERN_SOURCES
        and any(
            pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
            for pattern in SECRETS
        )
    ]
    assert offenders == []


@pytest.mark.parametrize("suffix", [".js", ".cjs", ".html", ".css"])
def test_frontend_assets_are_scanned_without_reporting_secret_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    asset = tmp_path / f"app{suffix}"
    asset.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic regression marker\n")
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)
    monkeypatch.setitem(globals(), "ROOT", tmp_path)

    assert check_repo.check_secrets() == [
        f"app{suffix} contains a secret or identifying value"
    ]
    assert _tracked_text_files() == [asset]


def test_the_manifest_names_the_public_integration_maintainer() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.pop("codeowners") == ["@memphi2"]
    assert "@" not in json.dumps(manifest)


def test_the_manifest_requests_only_reviewed_runtime_dependencies_as_minimums() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["requirements"] == ["pymodbus[serial]>=3.13.1"]


def test_standalone_service_keeps_its_explicit_cryptography_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cryptography>=48.0.1" in project["project"]["dependencies"]
    requirements = (ROOT / "requirements-service.txt").read_text(encoding="utf-8").splitlines()
    assert "cryptography==50.0.2" in requirements


def test_diagnostics_never_report_the_port_or_the_serial_number() -> None:
    import asyncio

    from custom_components.mennekes_amtron_rtu.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from tests.ha_fakes import build_runtime

    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.runtime_data.diagnostics.last_error = "ABC123456789 failed on /dev/fake"
        await entry.runtime_data.client.async_close()
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert "/dev/fake" not in repr(dump)
        assert "ABC123456789" not in repr(dump)
        assert "serial_number" not in dump["registers"]
        assert dump["device"]["serial_number_hash"]

    asyncio.run(run())


def test_no_vendor_document_is_tracked() -> None:
    assert list(ROOT.rglob("*.pdf")) == []


def test_the_brand_assets_are_the_reviewed_ones() -> None:
    import hashlib

    expected = "1ec09c77821817d235eb180da6ca006ab305074ffdcda0e2a44b7b85a4897c63"
    for name in ("icon.png", "logo.png"):
        digest = hashlib.sha256((COMPONENT / "brand" / name).read_bytes()).hexdigest()
        assert digest == expected
    assert expected in (ROOT / "docs" / "legal.md").read_text(encoding="utf-8")
