#!/usr/bin/env python3
"""Focused legal/provenance gate for release preparation."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from check_reporting import report_failures

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_DIRS = {
    "dist",
    "external",
    "extracted",
    "firmware",
    "node_modules",
    "third_party",
    "vendor",
}
FORBIDDEN_SUFFIXES = {
    ".7z",
    ".docx",
    ".epub",
    ".a",
    ".apk",
    ".bin",
    ".deb",
    ".gz",
    ".img",
    ".o",
    ".pcap",
    ".pcapng",
    ".pdf",
    ".rpm",
    ".so",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
}
LEGAL_PHRASES = (
    "No vendored vendor documentation",
    "Project-owned generic artwork",
    "Trademark notice",
    "not affiliated with, endorsed by, sponsored by, or certified by",
    "Apache License, Version 2.0",
    "No firmware or configuration-tool payloads",
)
NOTICE_PHRASES = (
    "not affiliated with, endorsed by, sponsored by, or certified by",
    "trademarks or names of their respective owners",
)
# Structural phrases of the manufacturer's Modbus RTU specification. A
# citation of its title carries none of them; a text extraction of the
# document carries all of them. Two are enough to call it a copy.
VENDOR_DOCUMENT_MARKERS = (
    "Release information",
    "Doc. Revision",
    "Modbus-Version:",
    "Internal Modbus Register Layout Version",
    "The following functional codes can be used",
    "Reading out this part will return basic information",
    "Minimum requirements for charging with an EMS",
    "Helpful information for implementing the wallbox into an energy",
)
VENDOR_DOCUMENT_MARKER_LIMIT = 2
# The files that define or exercise the markers necessarily contain them.
VENDOR_MARKER_SOURCES = {
    "scripts/check_legal_audit.py",
    "tests/test_gates.py",
}
TEXT_SUFFIXES = {
    ".csv",
    ".in",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

PROJECT_OWNED_BRAND_HASH = (
    "1ec09c77821817d235eb180da6ca006ab305074ffdcda0e2a44b7b85a4897c63"
)
BRAND_ASSETS = (
    "custom_components/mennekes_amtron/brand/icon.png",
    "custom_components/mennekes_amtron/brand/logo.png",
)
REQUIRED_DOCUMENTS = (
    "LICENSE",
    "NOTICE",
    "PRIVACY.md",
    "SECURITY.md",
    "docs/legal.md",
    "docs/audits/current-legal-provenance.md",
)


def main() -> int:
    """Run the legal and provenance gate."""

    failures: list[str] = []
    tracked = _tracked_files()
    if tracked is None:
        return 1
    if not tracked:
        # An empty inventory would let every payload check pass vacuously.
        sys.stderr.write("FAIL: no tracked files to audit\n")
        return 1

    failures.extend(_check_tracked_payloads(tracked))
    failures.extend(_check_vendor_documents(tracked))
    failures.extend(_check_required_documents())
    failures.extend(_check_runtime_requirements())
    failures.extend(_check_brand_assets())

    if failures:
        return report_failures(failures)

    classes = _extension_counts(tracked)
    sys.stdout.write("Legal/provenance audit passed\n")
    sys.stdout.write(f"tracked_files={len(tracked)}\n")
    sys.stdout.write(
        "tracked_extensions="
        + ", ".join(f"{key}:{classes[key]}" for key in sorted(classes))
        + "\n"
    )
    return 0


def _tracked_files() -> list[Path] | None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write("FAIL: unable to inspect tracked files\n")
        return None
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def _check_tracked_payloads(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for rel in paths:
        if any(part in FORBIDDEN_DIRS for part in rel.parts):
            failures.append(f"foreign/runtime directory must not be tracked: {rel}")
        if rel.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(
                f"forbidden binary/archive/document payload must not be "
                f"tracked: {rel}"
            )
    return failures


def _check_vendor_documents(paths: list[Path]) -> list[str]:
    """Reject a tracked copy of the manufacturer's documentation.

    The register addresses and values this integration implements are facts
    and are restated in the project's own words. The specification's prose is
    not, and a text extraction of the document is a copy of it whatever the
    file is called -- which the suffix list alone would not notice.
    """

    failures: list[str] = []
    for rel in paths:
        if rel.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if str(rel) in VENDOR_MARKER_SOURCES:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = [marker for marker in VENDOR_DOCUMENT_MARKERS if marker in text]
        if len(found) >= VENDOR_DOCUMENT_MARKER_LIMIT:
            failures.append(
                f"tracked file looks like a copy of the vendor specification "
                f"({', '.join(found[:3])}): {rel}"
            )
    return failures


def _check_required_documents() -> list[str]:
    failures: list[str] = []
    for rel in REQUIRED_DOCUMENTS:
        if not (ROOT / rel).is_file():
            failures.append(f"missing legal/provenance document: {rel}")
    audit_files = sorted(path.name for path in (ROOT / "docs" / "audits").glob("*.md"))
    if audit_files != ["current-legal-provenance.md"]:
        failures.append(
            "docs/audits must contain only current-legal-provenance.md, got "
            + ", ".join(audit_files)
        )
    legal_path = ROOT / "docs" / "legal.md"
    if legal_path.is_file():
        legal = _normalized_text(legal_path)
        for phrase in LEGAL_PHRASES:
            if phrase not in legal:
                failures.append(f"docs/legal.md must mention {phrase!r}")
    notice_path = ROOT / "NOTICE"
    if notice_path.is_file():
        notice = _normalized_text(notice_path)
        for phrase in NOTICE_PHRASES:
            if phrase not in notice:
                failures.append(f"NOTICE must mention {phrase!r}")
    readme_path = ROOT / "README.md"
    if readme_path.is_file():
        first_line = readme_path.read_text(encoding="utf-8").splitlines()[0]
        if "(Unofficial)" not in first_line:
            failures.append("README title must keep the project marked as unofficial")
    return failures


def _check_runtime_requirements() -> list[str]:
    """The manifest may request pymodbus, and nothing else.

    Every added runtime requirement is a dependency a user installs through
    Home Assistant, so it has to be a deliberate, reviewed decision.
    """

    manifest = json.loads(
        (ROOT / "custom_components" / "mennekes_amtron" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    requirements = [str(item) for item in manifest.get("requirements", [])]
    unexpected = [
        requirement
        for requirement in requirements
        if not requirement.startswith("pymodbus")
    ]
    if unexpected:
        return [
            "manifest.json runtime requirements must stay limited to pymodbus, "
            f"got {unexpected!r}"
        ]
    return []


def _check_brand_assets() -> list[str]:
    failures: list[str] = []
    for rel in BRAND_ASSETS:
        path = ROOT / rel
        if not path.is_file():
            failures.append(f"missing project-owned brand asset: {rel}")
            continue
        if _sha256(path) != PROJECT_OWNED_BRAND_HASH:
            failures.append(
                f"brand asset hash changed and docs/legal.md needs review: {rel}"
            )
    return failures


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _extension_counts(paths: list[Path]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in paths:
        counts[path.suffix or "[none]"] += 1
    return counts


if __name__ == "__main__":
    sys.exit(main())
