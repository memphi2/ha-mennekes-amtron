#!/usr/bin/env python3
"""Run the strict typing gate.

Unlike a grown integration this one starts with every module in the strict
set: there is no legacy code to ratchet away from.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "mennekes_amtron"


def strict_targets() -> list[str]:
    """Return every module the strict gate covers."""

    modules = sorted(
        str(path.relative_to(ROOT))
        for path in COMPONENT.glob("*.py")
    )
    scripts = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "scripts").glob("*.py")
    )
    return modules + scripts


def main() -> int:
    """Run mypy --strict over the strict target set."""

    # scripts/ import the integration as ``custom_components.mennekes_amtron``
    # while the package itself resolves as ``mennekes_amtron``. Anchoring the
    # package base at the repository root gives every file one module name.
    search_path = os.pathsep.join((str(ROOT), str(ROOT / "scripts")))
    environment = dict(os.environ, MYPYPATH=search_path)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--ignore-missing-imports",
            "--follow-imports=silent",
            "--explicit-package-bases",
            *strict_targets(),
        ],
        check=False,
        cwd=ROOT,
        env=environment,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
