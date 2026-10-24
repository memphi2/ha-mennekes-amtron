#!/usr/bin/env python3
"""Build the HACS release archive.

HACS extracts ``ha-mennekes-amtron.zip`` directly into
``config/custom_components/mennekes_amtron_rtu/``, so the archive contains
component files at its root. Fixed timestamps and permissions make repeated
builds of the same source byte-for-byte reproducible.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

from manifest_version import read_integration_version

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "mennekes_amtron_rtu"
COMPONENT_SRC = ROOT / "custom_components" / DOMAIN
RELEASE_ROOT = ROOT / ".release"
PACKAGE_ROOT = RELEASE_ROOT / "package"
HACS_ZIP_NAME = "ha-mennekes-amtron.zip"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

# Apache-2.0 asks for the licence and the notice to travel with the work.
# Nothing else belongs in a user's custom_components directory: the remaining
# documents live in the repository, where their links resolve.
PACKAGE_METADATA = ("LICENSE", "NOTICE")


def main() -> int:
    """Build the HACS release archive."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the archive. Defaults to .release.",
    )
    args = parser.parse_args()

    version = read_integration_version()
    _prepare_package()
    output_dir = args.output_dir or RELEASE_ROOT
    output_dir.mkdir(parents=True, exist_ok=True)

    hacs_zip = output_dir / HACS_ZIP_NAME
    _write_zip(hacs_zip)
    sys.stdout.write(f"Built {hacs_zip} for version {version}\n")
    return 0


def _prepare_package() -> None:
    if PACKAGE_ROOT.exists():
        shutil.rmtree(PACKAGE_ROOT)
    shutil.copytree(
        COMPONENT_SRC,
        PACKAGE_ROOT,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in PACKAGE_METADATA:
        shutil.copyfile(ROOT / name, PACKAGE_ROOT / name)


def _write_zip(output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE_ROOT.rglob("*")):
            if not path.is_file():
                continue
            name = str(path.relative_to(PACKAGE_ROOT))
            archive.writestr(_zip_info(name), path.read_bytes())


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


if __name__ == "__main__":
    sys.exit(main())
