#!/usr/bin/env python3
"""Build the HACS release zip.

The zip holds the integration package plus the legal metadata, with fixed
timestamps so two builds of the same commit produce the same bytes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

from manifest_version import read_integration_version

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "mennekes_amtron"
COMPONENT_SRC = ROOT / "custom_components" / DOMAIN
RELEASE_ROOT = ROOT / ".release"
PACKAGE_ROOT = RELEASE_ROOT / "package"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PACKAGE_METADATA = (
    "LICENSE",
    "NOTICE",
    "PRIVACY.md",
    "SECURITY.md",
    "docs/legal.md",
    "docs/safety.md",
)


def main() -> int:
    """Build the release zip."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip path. Defaults to .release/ha-mennekes-amtron.zip.",
    )
    args = parser.parse_args()

    version = read_integration_version()
    _prepare_package()
    output = args.output or RELEASE_ROOT / "ha-mennekes-amtron.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(output)
    sys.stdout.write(f"Built {output} for version {version}\n")
    return 0


def _prepare_package() -> None:
    if PACKAGE_ROOT.exists():
        shutil.rmtree(PACKAGE_ROOT)
    shutil.copytree(
        COMPONENT_SRC,
        PACKAGE_ROOT,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for rel in PACKAGE_METADATA:
        source = ROOT / rel
        target = PACKAGE_ROOT / Path(rel).name
        shutil.copyfile(source, target)


def _write_zip(output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE_ROOT.rglob("*")):
            if path.is_file():
                archive.writestr(_zip_info(path), path.read_bytes())


def _zip_info(path: Path) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(
        filename=str(path.relative_to(PACKAGE_ROOT)),
        date_time=ZIP_TIMESTAMP,
    )
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


if __name__ == "__main__":
    sys.exit(main())
