#!/usr/bin/env python3
"""Build the release archives.

Two archives, because two install paths want different shapes:

``ha-mennekes-amtron.zip``
    What HACS downloads. ``zip_release`` extracts the archive *into*
    ``config/custom_components/mennekes_amtron/``, so its contents have to sit
    at the archive root with no wrapping directory.

``ha-mennekes-amtron-manual.zip``
    What a human downloads. It carries the full
    ``custom_components/mennekes_amtron/`` path, so unpacking it over a Home
    Assistant configuration directory puts every file where it belongs
    instead of scattering fifty files into the download folder.

Both are built with fixed timestamps and permissions, so two builds of the
same commit produce the same bytes.
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
HACS_ZIP_NAME = "ha-mennekes-amtron.zip"
MANUAL_ZIP_NAME = "ha-mennekes-amtron-manual.zip"
COMPONENT_PREFIX = f"custom_components/{DOMAIN}"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

# Apache-2.0 asks for the licence and the notice to travel with the work.
# Nothing else belongs in a user's custom_components directory: the remaining
# documents live in the repository, where their links resolve.
PACKAGE_METADATA = ("LICENSE", "NOTICE")


def main() -> int:
    """Build both release archives."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the archives. Defaults to .release.",
    )
    args = parser.parse_args()

    version = read_integration_version()
    _prepare_package()
    output_dir = args.output_dir or RELEASE_ROOT
    output_dir.mkdir(parents=True, exist_ok=True)

    hacs_zip = output_dir / HACS_ZIP_NAME
    manual_zip = output_dir / MANUAL_ZIP_NAME
    _write_zip(hacs_zip, prefix="")
    _write_zip(manual_zip, prefix=COMPONENT_PREFIX)
    sys.stdout.write(f"Built {hacs_zip} for version {version}\n")
    sys.stdout.write(f"Built {manual_zip} for version {version}\n")
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


def _write_zip(output: Path, *, prefix: str) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE_ROOT.rglob("*")):
            if not path.is_file():
                continue
            name = str(path.relative_to(PACKAGE_ROOT))
            archive.writestr(_zip_info(f"{prefix}/{name}" if prefix else name), path.read_bytes())


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


if __name__ == "__main__":
    sys.exit(main())
