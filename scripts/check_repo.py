#!/usr/bin/env python3
"""Local repository validation for ha-mennekes-amtron."""

from __future__ import annotations

import compileall
import json
import re
import sys
from pathlib import Path

from check_reporting import report_failures

ROOT = Path(__file__).resolve().parents[1]
VERSION_CONFIG_PATH = ROOT / "project-versions.json"
COMPONENT = ROOT / "custom_components" / "mennekes_amtron"

REQUIRED_PATHS = (
    "LICENSE",
    "NOTICE",
    "PRIVACY.md",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "CHANGELOG.md",
    "hacs.json",
    "project-versions.json",
    "pyproject.toml",
    "requirements-dev.in",
    "requirements-dev.txt",
    "requirements-dev-min-ha.txt",
    "custom_components/mennekes_amtron/manifest.json",
    "custom_components/mennekes_amtron/quality_scale.yaml",
    "custom_components/mennekes_amtron/strings.json",
    "custom_components/mennekes_amtron/icons.json",
    "custom_components/mennekes_amtron/services.yaml",
    "custom_components/mennekes_amtron/translations/en.json",
    "custom_components/mennekes_amtron/translations/de.json",
    "docs/quickstart.md",
    "docs/user-guide.md",
    "docs/modbus-registers.md",
    "docs/safety.md",
    "docs/architecture.md",
    "docs/quality-scale.md",
    "docs/legal.md",
    "docs/audits/current-legal-provenance.md",
    ".github/workflows/validate.yml",
    ".github/workflows/release.yml",
    ".github/workflows/codeql.yml",
    ".github/dependabot.yml",
)

FORBIDDEN_TRACKED_DIRS = ("__pycache__", ".venv", ".release", "htmlcov")
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ssh-rsa AAAA"),
    # A real by-id path carries the adapter's serial number.
    re.compile(r"/dev/serial/by-id/usb-[A-Za-z0-9_.-]{12,}"),
)
TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml", ".in"}
# The files that define or exercise the patterns above necessarily contain
# them; every other file must not.
SECRET_PATTERN_SOURCES = {
    "scripts/check_repo.py",
    "tests/test_privacy_security.py",
    "tests/test_flow_serial.py",
}
SHA_PINNED_USES = re.compile(r"uses: [^@\s]+@(?P<ref>[^\s]+)")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_version_config() -> dict[str, str]:
    data = json.loads(VERSION_CONFIG_PATH.read_text(encoding="utf-8"))
    required_keys = {
        "min_homeassistant",
        "current_homeassistant",
        "python",
        "pymodbus",
        "amtron_modbus_layout",
        "integration_version",
    }
    missing = sorted(required_keys.difference(data))
    if missing:
        raise RuntimeError(
            f"project-versions.json is missing keys: {', '.join(missing)}"
        )
    return {key: str(data[key]) for key in required_keys}


VERSION_CONFIG = _load_version_config()


def main() -> int:
    """Run every repository check."""

    failures: list[str] = []
    failures.extend(check_required_paths())
    failures.extend(check_tracked_runtime_artifacts())
    failures.extend(check_json_files())
    failures.extend(check_release_metadata())
    failures.extend(check_hacs_metadata())
    failures.extend(check_requirement_pins())
    failures.extend(check_github_automation())
    failures.extend(check_secrets())
    failures.extend(check_python_compile())
    return report_failures(failures, "Repository checks passed")


def check_required_paths() -> list[str]:
    """Every file the project promises has to exist."""

    return [
        f"missing required path: {rel}"
        for rel in REQUIRED_PATHS
        if not (ROOT / rel).exists()
    ]


def check_tracked_runtime_artifacts() -> list[str]:
    """Build and cache directories must not live inside the package."""

    failures: list[str] = []
    for name in FORBIDDEN_TRACKED_DIRS:
        for path in ROOT.rglob(name):
            if ".git" in path.parts:
                continue
            if path.is_dir() and _is_tracked(path):
                failures.append(f"runtime artifact directory is tracked: {path}")
    return failures


def check_json_files() -> list[str]:
    """Every tracked JSON file has to parse."""

    failures: list[str] = []
    for path in sorted(ROOT.rglob("*.json")):
        if _is_ignored(path):
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            failures.append(f"invalid JSON in {_relative(path)}: {err}")
    return failures


def check_release_metadata() -> list[str]:
    """The manifest version is the single release version."""

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    expected = VERSION_CONFIG["integration_version"]
    if manifest.get("version") != expected:
        failures.append(
            f"manifest.json version {manifest.get('version')!r} does not match "
            f"project-versions.json {expected!r}"
        )
    if manifest.get("quality_scale") != "platinum":
        failures.append("manifest.json must declare the platinum quality scale")
    if manifest.get("codeowners") != []:
        failures.append("manifest.json must not carry personal metadata")
    if manifest.get("iot_class") != "local_polling":
        failures.append("manifest.json iot_class must stay local_polling")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {expected}" not in changelog:
        failures.append(f"CHANGELOG.md has no section for release {expected}")
    return failures


def check_hacs_metadata() -> list[str]:
    """HACS metadata has to agree with the supported Home Assistant range."""

    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    if hacs.get("homeassistant") != VERSION_CONFIG["min_homeassistant"]:
        failures.append(
            "hacs.json homeassistant must match project-versions.json "
            f"{VERSION_CONFIG['min_homeassistant']}"
        )
    if hacs.get("filename") != "ha-mennekes-amtron.zip":
        failures.append("hacs.json filename must stay ha-mennekes-amtron.zip")
    if not hacs.get("zip_release"):
        failures.append("hacs.json must keep zip_release enabled")
    return failures


def check_requirement_pins() -> list[str]:
    """The dev locks have to pin the same pymodbus as the manifest."""

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    pinned = next(
        (
            requirement
            for requirement in manifest.get("requirements", [])
            if str(requirement).startswith("pymodbus")
        ),
        None,
    )
    failures: list[str] = []
    if pinned is None:
        return ["manifest.json must pin pymodbus"]
    for rel in ("requirements-dev.in", "requirements-dev.txt", "requirements-dev-min-ha.txt"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if pinned not in text:
            failures.append(f"{rel} must pin {pinned}")
    return failures


def check_github_automation() -> list[str]:
    """CI has to stay pinned, reproducible and aligned with the versions."""

    failures: list[str] = []
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    if not workflows:
        return ["no GitHub workflow is tracked"]
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        if "ubuntu-latest" in text:
            failures.append(f"{_relative(path)} must pin a runner, not ubuntu-latest")
        for match in SHA_PINNED_USES.finditer(text):
            ref = match.group("ref")
            if not FULL_SHA.match(ref):
                failures.append(
                    f"{_relative(path)} uses an action pinned to {ref!r} "
                    "instead of a 40-character commit SHA"
                )
    validate = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    for key in ("min_homeassistant", "current_homeassistant", "python"):
        value = VERSION_CONFIG[key]
        if f'"{value}"' not in validate:
            failures.append(f"validate.yml does not pin {key} {value}")
    dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    for ecosystem in ("github-actions", "pip"):
        if f'package-ecosystem: "{ecosystem}"' not in dependabot:
            failures.append(f"dependabot.yml does not cover {ecosystem}")
    return failures


def check_secrets() -> list[str]:
    """No tracked text file may carry a key or an identifying device path."""

    failures: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or _is_ignored(path):
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        if _relative(path) in SECRET_PATTERN_SOURCES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(
                    f"{_relative(path)} contains a secret or identifying value"
                )
                break
    return failures


def check_python_compile() -> list[str]:
    """Every tracked Python file has to compile."""

    ok = compileall.compile_dir(
        str(COMPONENT), quiet=2, force=True, legacy=False
    ) and compileall.compile_dir(
        str(ROOT / "scripts"), quiet=2, force=True, legacy=False
    )
    return [] if ok else ["python sources failed to compile"]


def _is_tracked(path: Path) -> bool:
    return not _is_ignored(path)


def _is_ignored(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts if path.is_absolute() else path.parts
    ignored = {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".release",
        "__pycache__",
        "htmlcov",
    }
    return any(part in ignored for part in parts)


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


if __name__ == "__main__":
    sys.exit(main())
