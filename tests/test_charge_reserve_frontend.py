"""Execute the shipped reserve controls without replacing their JavaScript."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_reserve_frontend_protocol():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for offline frontend protocol tests")
    subprocess.run(
        [node, "--test", str(Path(__file__).with_name("reserve_frontend.test.cjs"))],
        check=True, capture_output=True, text=True, timeout=30,
    )
