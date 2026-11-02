"""Check that the stable maintenance job really installed the latest stable HA."""

from __future__ import annotations

import json
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from packaging.version import Version


def validate_release(release: dict[str, Any], installed: str) -> None:
    """Do not silently test an older Python-compatible fallback or a beta."""
    expected = Version(release["tag_name"])
    actual = Version(installed)
    if (release.get("draft") is not False or release.get("prerelease") is not False
            or expected.is_prerelease or expected.is_devrelease
            or actual != expected):
        raise ValueError("installed HA does not match the latest stable release")


def main() -> int:
    try:
        result = subprocess.run(
            ["gh", "api", "repos/home-assistant/core/releases/latest"],
            capture_output=True, check=True, text=True,
        )
        installed = version("homeassistant")
        validate_release(json.loads(result.stdout), installed)
    except (OSError, subprocess.CalledProcessError, PackageNotFoundError, ValueError, KeyError, TypeError):
        sys.stderr.write("Cannot verify latest stable HA; check release availability and Python/dependency compatibility.\n")
        return 1
    sys.stdout.write(f"Latest stable HA verified: {installed}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
