#!/usr/bin/env python3
"""Read the integration version from the manifest."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "mennekes_amtron" / "manifest.json"


def read_integration_version() -> str:
    """Return the version declared in manifest.json."""

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("manifest.json does not declare a version")
    return version
