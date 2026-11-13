"""Execute the shipped job UI with deterministic HTTP and DOM fixtures."""

import re
import shutil
import subprocess
from pathlib import Path

from custom_components.mennekes_amtron_rtu.device.charge_job import REASONS, STATES


def test_job_frontend_contract():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the shipped UI tests"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("charge_jobs_frontend.test.cjs"))],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_job_state_and_reason_has_a_label_in_all_five_languages():
    device = Path(__file__).parents[1] / "custom_components" / "mennekes_amtron_rtu" / "device"
    for page, keys in (("app_jobs.js", (*STATES, *REASONS)), ("app_guest.js", STATES)):
        source = (device / page).read_text(encoding="utf-8")
        for key in keys:
            match = re.search(rf"^    {key}:\[(.*)\],?$", source, re.MULTILINE)
            assert match is not None, (page, key)
            assert len(re.findall(r'"(?:[^"\\]|\\.)+"', match.group(1))) == 5, (page, key)
