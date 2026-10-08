"""Meta tests: the repository must not ship identifying or secret values."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "mennekes_amtron"
TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml", ".in"}
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
        and path.suffix in TEXT_SUFFIXES
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


def test_the_manifest_ships_no_personal_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["codeowners"] == []
    assert "@" not in json.dumps(manifest)


def test_the_manifest_requests_only_pymodbus_and_only_as_a_minimum() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["requirements"] == ["pymodbus[serial]>=3.13.1"]


def test_diagnostics_never_report_the_port_or_the_serial_number() -> None:
    source = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "port_category(" in source
    assert "fnv1a64(identity.serial_number)" in source
    assert "client.config.port," not in source


def test_no_vendor_document_is_tracked() -> None:
    assert list(ROOT.rglob("*.pdf")) == []


def test_the_brand_assets_are_the_reviewed_ones() -> None:
    import hashlib

    expected = "1ec09c77821817d235eb180da6ca006ab305074ffdcda0e2a44b7b85a4897c63"
    for name in ("icon.png", "logo.png"):
        digest = hashlib.sha256((COMPONENT / "brand" / name).read_bytes()).hexdigest()
        assert digest == expected
    assert expected in (ROOT / "docs" / "legal.md").read_text(encoding="utf-8")
