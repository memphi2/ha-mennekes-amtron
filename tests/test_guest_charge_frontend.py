"""Run the shipped guest page script against deterministic browser doubles."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_guest_frontend():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the shipped UI tests"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("guest_charge_frontend.test.cjs"))],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
