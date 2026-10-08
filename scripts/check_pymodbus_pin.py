#!/usr/bin/env python3
"""Keep the pymodbus requirement compatible with Home Assistant Core.

Home Assistant installs one pymodbus for the whole instance and Core's own
``modbus`` integration pins an exact version, which moves between Home
Assistant releases; every release in the supported range currently resolves
3.13.1. An exact pin here would fight Core the moment that moves again, so
``manifest.json`` declares a *minimum* and lets Home Assistant own the
resolved version.

The gate checks that the minimum is really a minimum, that it matches
``project-versions.json``, and that the installed Home Assistant resolves to a
version at or above it.
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
REQUIREMENT_RE = re.compile(
    r"^pymodbus(?:\[[a-z,]+\])?(?P<operator>[><=]=)(?P<version>[0-9.]+)$"
)


def main() -> int:
    """Compare every place the pymodbus version is recorded."""

    return report_failures(check_pin(), "pymodbus requirement validation passed")


def check_pin() -> list[str]:
    """Return every problem with the declared pymodbus requirement."""

    failures: list[str] = []
    requirement = manifest_requirement()
    if requirement is None:
        return ["manifest.json does not declare a pymodbus requirement"]
    operator, minimum = requirement

    if operator != ">=":
        failures.append(
            "manifest.json must declare a pymodbus minimum with '>=', not "
            f"'{operator}': an exact pin fights the pin of Home Assistant "
            "Core's own modbus integration"
        )

    declared = json.loads(VERSIONS.read_text(encoding="utf-8")).get(
        "pymodbus_minimum"
    )
    if declared != minimum:
        failures.append(
            f"project-versions.json says pymodbus_minimum {declared}, "
            f"manifest.json declares {minimum}"
        )

    core = core_version()
    if core is None:
        sys.stdout.write(
            "Home Assistant is not installed; skipping the Core comparison\n"
        )
    elif _as_tuple(core) < _as_tuple(minimum):
        failures.append(
            f"Home Assistant Core resolves pymodbus {core}, which is below the "
            f"declared minimum {minimum}"
        )
    return failures


def manifest_requirement() -> tuple[str, str] | None:
    """Return the operator and version of the pymodbus requirement."""

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for requirement in manifest.get("requirements", []):
        match = REQUIREMENT_RE.match(str(requirement))
        if match:
            return match.group("operator"), match.group("version")
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
    manifest_path = Path(str(core_components.__file__)).parent / "modbus" / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for requirement in manifest.get("requirements", []):
        match = REQUIREMENT_RE.match(str(requirement))
        if match:
            return match.group("version")
    return None


def _as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


if __name__ == "__main__":
    sys.exit(main())
