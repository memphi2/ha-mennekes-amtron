"""Stable-channel identity and focused API regression checks."""

from pathlib import Path

import pytest
import yaml

from scripts.check_deprecations import source_issues
from scripts.check_ha_channel import main as check_ha
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


@pytest.mark.parametrize("workflow", ["validate.yml", "release.yml"])
def test_required_matrix_is_minimum_and_dynamic_latest_only(workflow):
    jobs = yaml.safe_load((Path(".github/workflows") / workflow).read_text())["jobs"]
    entries = jobs["validate"]["strategy"]["matrix"]["include"]
    assert [entry["name"] for entry in entries] == ["min-ha", "latest-ha"]
    assert entries[0]["homeassistant"] == "2026.9.0"
    assert entries[0]["validate-arguments"] == "--skip-typing"
    assert entries[1]["homeassistant"] == "latest"
    assert entries[1]["validate-arguments"] == ""
    commands = "\n".join(step.get("run", "") for step in jobs["validate"]["steps"])
    assert "--pre" not in commands
    assert "--upgrade homeassistant" in commands
    assert commands.index('check_ha_channel.py --expected "$HA_TARGET"') > commands.index("pip check")
    assert "latest-stable-ha" not in jobs


def test_preview_is_weekly_or_manual_and_soak_uses_latest():
    jobs = yaml.safe_load(Path(".github/workflows/validate.yml").read_text())["jobs"]
    preview = jobs["next-ha"]
    assert preview["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert not preview.get("continue-on-error")
    assert "--pre --upgrade homeassistant" in str(preview)
    assert "--upgrade homeassistant" in str(jobs["runtime-soak"])
    assert "check_ha_channel.py" in str(jobs["runtime-soak"])


@pytest.mark.parametrize("installed", ["2026.9.0", "2026.10.4"])
def test_minimum_identity_is_checked_without_network(monkeypatch, installed):
    from scripts import check_ha_channel

    monkeypatch.setattr(check_ha_channel, "version", lambda _package: installed)
    def no_network(*_args, **_kwargs):
        pytest.fail("The fixed minimum does not need a release API request")
    monkeypatch.setattr(check_ha_channel.subprocess, "run", no_network)
    assert check_ha(["--expected", "2026.9.0"]) == int(installed != "2026.9.0")


def test_ci_cancels_only_superseded_prs_and_serializes_release_publication():
    validate = yaml.safe_load(Path(".github/workflows/validate.yml").read_text())
    release = yaml.safe_load(Path(".github/workflows/release.yml").read_text())
    assert validate["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.run_id" in validate["concurrency"]["group"]
    assert release["concurrency"]["cancel-in-progress"] is False
    assert "github.event.inputs.tag || github.ref_name" in release["concurrency"]["group"]
    for workflow in (validate, release):
        assert all(job.get("timeout-minutes", 0) > 0 for job in workflow["jobs"].values())


@pytest.mark.parametrize("job_name", ["validate", "publish"])
def test_tag_release_jobs_cannot_share_dependency_caches(job_name):
    """A moved tag must not feed cached executables to a privileged publisher."""

    release = yaml.safe_load(Path(".github/workflows/release.yml").read_text())
    assert release["env"]["PIP_NO_CACHE_DIR"] == "1"
    steps = release["jobs"][job_name]["steps"]
    assert any(step.get("uses", "").startswith("actions/setup-python@") for step in steps)
    for step in steps:
        assert not step.get("uses", "").startswith("actions/cache")
        if step.get("uses", "").startswith("actions/setup-python@"):
            assert not step.get("with", {}).get("cache")
