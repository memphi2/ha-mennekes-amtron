"""Exercise the actual shipped configuration UI with offline Node fixtures."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_runtime_settings_frontend():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the optional UI interaction tests")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("runtime_settings_frontend.test.cjs"))],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
