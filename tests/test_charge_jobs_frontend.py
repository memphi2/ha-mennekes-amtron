"""Execute the shipped job UI with deterministic HTTP and DOM fixtures."""

import subprocess
from pathlib import Path


def test_job_frontend_contract():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("charge_jobs_frontend.test.cjs"))],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
