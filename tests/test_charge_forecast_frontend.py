"""Execute the shipped advisory UI without substituting its JavaScript."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_forecast_frontend_protocol():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for offline frontend protocol tests")
    subprocess.run(
        [node, "--test", str(Path(__file__).with_name("forecast_frontend.test.cjs"))],
        check=True, capture_output=True, text=True, timeout=30,
    )
