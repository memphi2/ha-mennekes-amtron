#!/usr/bin/env python3
"""Export a local Supervisor app/Docker build context from the shared driver."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(destination: Path) -> None:
    """Create a new build context, refusing to overwrite an existing directory."""
    destination.mkdir(parents=True, exist_ok=False)
    for filename in (
        "Dockerfile",
        "pyproject.toml",
        "requirements-service.txt",
        "LICENSE",
        "NOTICE",
    ):
        shutil.copy2(ROOT / filename, destination / filename)
    # Local exports build this checkout, even after published images exist.
    config = (ROOT / "modbus_service/config.yaml").read_text()
    (destination / "config.yaml").write_text(
        "".join(
            line
            for line in config.splitlines(keepends=True)
            if not line.startswith("image:")
        )
    )
    shutil.copy2(ROOT / "modbus_service/README.md", destination / "README.md")
    shutil.copy2(ROOT / "modbus_service/DOCS.md", destination / "DOCS.md")
    shutil.copy2(ROOT / "modbus_service/CHANGELOG.md", destination / "CHANGELOG.md")
    shutil.copy2(ROOT / "modbus_service/apparmor.txt", destination / "apparmor.txt")
    shutil.copytree(ROOT / "modbus_service/translations", destination / "translations")
    target = destination / "custom_components/mennekes_amtron_rtu/device"
    target.mkdir(parents=True)
    for source in (ROOT / "custom_components/mennekes_amtron_rtu/device").glob("*.py"):
        shutil.copy2(source, target / source.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.output)


if __name__ == "__main__":
    main()
