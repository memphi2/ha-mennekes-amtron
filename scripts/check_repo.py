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
    "docs/README.md",
    "docs/quickstart.md",
    "docs/hardware.md",
    "docs/user-guide.md",
    "docs/entities.md",
    "docs/troubleshooting.md",
    "docs/automations.md",
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
    "docs/repository-settings.md",
    "scripts/apply_repo_settings.py",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/support_question.yml",
)
ISSUE_FORMS = (
    "bug_report.yml",
    "feature_request.yml",
    "support_question.yml",
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
        "next_homeassistant",
        "python",
        "pymodbus_minimum",
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
    failures.extend(check_issue_templates())
    failures.extend(check_branch_protection_contexts())
    failures.extend(check_hardware_document())
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
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    if hacs.get("name") != manifest.get("name"):
        failures.append(
            f"hacs.json name {hacs.get('name')!r} and manifest.json name "
            f"{manifest.get('name')!r} disagree; both are shown to users"
        )
    if "(Unofficial)" not in str(manifest.get("name", "")):
        failures.append(
            "manifest.json name must mark the integration as unofficial: it is "
            "what Home Assistant shows in the integration list"
        )
    if hacs.get("filename") != "ha-mennekes-amtron.zip":
        failures.append("hacs.json filename must stay ha-mennekes-amtron.zip")
    if not hacs.get("zip_release"):
        failures.append("hacs.json must keep zip_release enabled")
    return failures


def check_requirement_pins() -> list[str]:
    """The dev locks pin exactly; the manifest declares a minimum.

    Home Assistant Core pins pymodbus itself and moves that pin between
    releases, so the manifest may only declare a floor. Each validation lock
    pins the version of its own Home Assistant matrix entry, and the minimum
    lock is what the manifest floor has to match.
    """

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    requirement = next(
        (
            str(item)
            for item in manifest.get("requirements", [])
            if str(item).startswith("pymodbus")
        ),
        None,
    )
    if requirement is None:
        return ["manifest.json must declare a pymodbus requirement"]
    if "==" in requirement:
        return [
            (
                "manifest.json must declare a pymodbus minimum, not an exact "
                "pin: Home Assistant Core pins pymodbus for the whole instance"
            )
        ]

    minimum = VERSION_CONFIG["pymodbus_minimum"]
    failures: list[str] = []
    if f">={minimum}" not in requirement:
        failures.append(
            f"manifest.json must declare pymodbus >={minimum}, got {requirement}"
        )
    locks = {
        "requirements-dev-min-ha.txt": minimum,
        "requirements-dev.txt": None,
    }
    for rel, expected in locks.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "pymodbus" not in text:
            failures.append(f"{rel} must pin pymodbus")
        elif expected and f"pymodbus[serial]=={expected}" not in text:
            failures.append(f"{rel} must pin pymodbus[serial]=={expected}")
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
    for key in (
        "min_homeassistant",
        "current_homeassistant",
        "next_homeassistant",
        "python",
    ):
        value = VERSION_CONFIG[key]
        if value not in validate:
            failures.append(f"validate.yml does not pin {key} {value}")
    dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    failures.extend(
        f"dependabot.yml does not cover {ecosystem}"
        for ecosystem in ("github-actions", "pip")
        if f'package-ecosystem: "{ecosystem}"' not in dependabot
    )
    return failures


def check_branch_protection_contexts() -> list[str]:
    """The protected status checks have to be jobs that really exist.

    Branch protection silently protects nothing when a required check is
    named after a job that was renamed or removed.
    """

    sys.path.insert(0, str(ROOT / "scripts"))
    from apply_repo_settings import REQUIRED_CHECKS

    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    failures: list[str] = []
    for context in REQUIRED_CHECKS:
        job, _, matrix_entry = context.partition(" (")
        if f"  {job}:" not in workflow:
            failures.append(
                f"branch protection requires the check {context!r}, but "
                f"validate.yml has no job {job!r}"
            )
        elif matrix_entry and f"name: {matrix_entry.rstrip(')')}" not in workflow:
            failures.append(
                f"branch protection requires the check {context!r}, but "
                f"validate.yml has no matrix entry {matrix_entry.rstrip(')')!r}"
            )
    return failures


def check_hardware_document() -> list[str]:
    """The hardware guide states facts that have to stay sourced.

    It mixes what the Modbus specification says with what the installation
    manual says, and this project does not have the manual. The markers are
    what keeps a later edit from quietly turning a guess into a claim.
    """

    text = (ROOT / "docs" / "hardware.md").read_text(encoding="utf-8")
    failures: list[str] = [
        f"docs/hardware.md must keep {phrase!r}"
        for phrase in (
            "**(Spec)**",
            "**(Manual)**",
            "Have an electrician do the work",
            "bank S1, DIP 4 and DIP 5",
            "RS-485 allows exactly one master",
        )
        if phrase not in text
    ]
    if "XG1" not in text:
        failures.append(
            "docs/hardware.md must document the XG1 downgrade input"
        )
    return failures


def check_issue_templates() -> list[str]:
    """Issue reports have to ask for what an analysis actually needs.

    Without the control mode, the register layout and the state of the bus,
    a report about this integration cannot be acted on, so the forms are
    checked for those fields instead of only for their existence.
    """

    directory = ROOT / ".github" / "ISSUE_TEMPLATE"
    failures: list[str] = []

    config = (directory / "config.yml").read_text(encoding="utf-8")
    if "blank_issues_enabled: false" not in config:
        failures.append(".github/ISSUE_TEMPLATE/config.yml must disable blank issues")

    required_fields = {
        "bug_report.yml": (
            "integration_version",
            "home_assistant_version",
            "modbus_layout",
            "control_mode",
            "diagnostics",
        ),
        "support_question.yml": ("integration_version", "control_mode"),
        "feature_request.yml": ("registers",),
    }
    for name in ISSUE_FORMS:
        text = (directory / name).read_text(encoding="utf-8")
        if "name:" not in text or "body:" not in text:
            failures.append(f".github/ISSUE_TEMPLATE/{name} is not an issue form")
            continue
        failures.extend(
            f".github/ISSUE_TEMPLATE/{name} must ask for {field}"
            for field in required_fields[name]
            if f"id: {field}" not in text
        )
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
