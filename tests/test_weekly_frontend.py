"""Exercise the shipped weekly-plan script without browser dependencies."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_weekly_frontend_protocol():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend protocol tests")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--test", str(root / "tests/weekly_frontend.test.cjs")],
                   cwd=root, check=True, timeout=30)
