#!/usr/bin/env python3
"""Keep the pymodbus pin aligned with Home Assistant Core.

Home Assistant installs one pymodbus version for the whole instance. If this
integration pins a different one than Core's own modbus integration, the two
fight over the same package and one of them loses at install time. The gate
compares the manifest pin with the pin of the installed Home Assistant and
with project-versions.json.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from check_reporting import report_failures

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "mennekes_amtron" / "manifest.json"
VERSIONS = ROOT / "project-versions.json"
REQUIREMENT_RE = re.compile(r"^pymodbus(?:\[[a-z,]+\])?==(?P<version>[0-9.]+)$")


def main() -> int:
    """Compare every place the pymodbus version is recorded."""

    return report_failures(check_pin(), "pymodbus pin validation passed")


def check_pin() -> list[str]:
    """Return every disagreement about the pinned pymodbus version."""

    failures: list[str] = []
    manifest_pin = manifest_version()
    if manifest_pin is None:
        return ["manifest.json does not pin pymodbus with =="]

    declared = json.loads(VERSIONS.read_text(encoding="utf-8")).get("pymodbus")
    if declared != manifest_pin:
        failures.append(
            f"project-versions.json says pymodbus {declared}, "
            f"manifest.json pins {manifest_pin}"
        )

    core_pin = core_version()
    if core_pin is None:
        sys.stdout.write(
            "Home Assistant is not installed; skipping the Core pin comparison\n"
        )
    elif core_pin != manifest_pin:
        failures.append(
            f"Home Assistant Core pins pymodbus {core_pin}, "
            f"manifest.json pins {manifest_pin}"
        )
    return failures


def manifest_version() -> str | None:
    """Return the pymodbus version pinned in the integration manifest."""

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for requirement in manifest.get("requirements", []):
        match = REQUIREMENT_RE.match(str(requirement))
        if match:
            return match.group("version")
    return None


def core_version() -> str | None:
    """Return the pymodbus version the installed Home Assistant pins."""

    # The modbus package is read as a file, never imported: importing it
    # pulls in Core's own runtime dependencies, which a validation
    # environment does not have.
    try:
        import homeassistant.components as core_components
    except ImportError:
        return None
    package = Path(str(core_components.__file__)).parent / "modbus"
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for requirement in manifest.get("requirements", []):
        match = REQUIREMENT_RE.match(str(requirement))
        if match:
            return match.group("version")
    return None


if __name__ == "__main__":
    sys.exit(main())
