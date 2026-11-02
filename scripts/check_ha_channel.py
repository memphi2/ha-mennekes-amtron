"""Verify the installed HA target after dependency resolution."""

from __future__ import annotations

import argparse
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", default="latest", help="Exact version or latest (stable)")
    args = parser.parse_args(argv)
    try:
        installed = version("homeassistant")
        if args.expected == "latest":
            result = subprocess.run(
                ["gh", "api", "repos/home-assistant/core/releases/latest"],
                capture_output=True, check=True, text=True, timeout=60,
            )
            validate_release(json.loads(result.stdout), installed)
        elif Version(installed) != Version(args.expected):
            raise ValueError("installed HA differs from the requested minimum")
    except (OSError, subprocess.SubprocessError, PackageNotFoundError, ValueError, KeyError, TypeError):
        sys.stderr.write("Cannot verify HA target; check release availability and Python/dependency compatibility.\n")
        return 1
    sys.stdout.write(f"HA target {args.expected} verified: {installed}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
