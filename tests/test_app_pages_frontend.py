"""Execute the shipped register, log and forecast UI with DOM fixtures."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", [
    "registers_frontend.test.cjs", "log_frontend.test.cjs", "forecast_frontend.test.cjs",
    "overview_frontend.test.cjs",
])
def test_app_page_frontend_contract(script: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the shipped UI tests"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name(script))],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
