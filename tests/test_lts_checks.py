"""Stable-channel identity and focused API regression checks."""

from pathlib import Path

import pytest
import yaml

from scripts.check_deprecations import source_issues
from scripts.check_ha_channel import validate_release


@pytest.mark.parametrize("installed", ["2026.10.0", "2026.11.0b1"])
def test_stable_job_rejects_old_or_preview_installs(installed):
    with pytest.raises(ValueError, match="latest stable"):
        validate_release({"tag_name": "2026.10.4", "draft": False, "prerelease": False}, installed)


def test_stable_job_accepts_exact_stable_and_rejects_draft_metadata():
    release = {"tag_name": "2026.10.4", "draft": False, "prerelease": False}
    validate_release(release, "2026.10.4")
    with pytest.raises(ValueError, match="latest stable"):
        validate_release(release | {"draft": True}, "2026.10.4")


@pytest.mark.parametrize("source", [
    "import asyncio as aio\naio.iscoroutinefunction(f)",
    "from datetime import datetime as dt\ndt.utcnow()",
    "entry.async_forward_entry_setup()",
    "import warnings\nwarnings.simplefilter('ignore')",
    "import warnings\nwarnings.filterwarnings('ignore', category=DeprecationWarning)",
    "import pytest\npytest.mark.filterwarnings('ignore::DeprecationWarning')",
])
def test_obsolete_apis_and_suppressed_warnings_fail(source):
    assert source_issues(source)


def test_prose_test_vectors_and_unrelated_warnings_are_not_deprecations():
    assert not source_issues('text = "datetime.utcnow()"\n# asyncio.iscoroutinefunction(f)')
    assert not source_issues("from homeassistant.util import dt\ndt.utcnow()")
    assert not source_issues("import warnings\nwarnings.simplefilter('ignore', UserWarning)")


def test_latest_stable_and_preview_are_separate_without_pin_changes():
    jobs = yaml.safe_load(Path(".github/workflows/validate.yml").read_text())["jobs"]
    stable = jobs["latest-stable-ha"]
    commands = "\n".join(step.get("run", "") for step in stable["steps"])
    assert "schedule" in stable["if"]
    assert "workflow_dispatch" in stable["if"]
    assert "--pre" not in commands
    assert "check_ha_channel.py" in commands
    assert "--upgrade homeassistant" in commands
    assert "--pre" in str(jobs["next-ha"])
