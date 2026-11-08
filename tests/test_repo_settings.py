"""Repository protection updates preserve existing policy and fail closed."""

from __future__ import annotations

import json
from subprocess import CompletedProcess

import pytest

from scripts import apply_repo_settings as settings


def test_status_checks_preserve_custom_contexts_and_app_identity() -> None:
    current = {
        "strict": False,
        "contexts": ["custom", "legacy", "hacs"],
        "checks": [
            {"context": "custom", "app_id": 42},
            {"context": "hacs", "app_id": 15368},
        ],
    }
    merged = settings.merged_status_checks(current)
    assert merged["strict"] is True
    assert merged["checks"][:2] == current["checks"]
    assert {check["context"] for check in merged["checks"]} == set(
        settings.REQUIRED_CHECKS
    ) | {"custom", "legacy"}
    assert current["strict"] is False
    assert settings.merged_status_checks(merged) == merged


def test_retired_owned_contexts_are_removed_without_weakening_other_checks():
    protected = [
        {"context": "custom", "app_id": 42},
        {"context": "independent-security-audit", "app_id": 15368},
    ]
    current = {
        "strict": True,
        "contexts": [*settings.RETIRED_CHECKS, "additional-review"],
        "checks": [*protected, *[
            {"context": name, "app_id": 15368} for name in settings.RETIRED_CHECKS
        ]],
    }
    merged = settings.merged_status_checks(current)
    assert merged["checks"][:2] == protected
    assert {check["context"] for check in merged["checks"]} == set(
        settings.REQUIRED_CHECKS
    ) | {"custom", "independent-security-audit", "additional-review"}
    assert settings.merged_status_checks(merged) == merged


@pytest.mark.parametrize("retired", sorted(settings.RETIRED_CHECKS))
def test_retired_checks_count_as_policy_drift(monkeypatch, retired):
    current = {"strict": True, "contexts": [*settings.REQUIRED_CHECKS, retired]}
    monkeypatch.setattr(settings, "_read_required_checks", lambda _path: (current, None))
    assert settings.main(["--check"]) == 1


@pytest.mark.parametrize("read_fails", [False, True])
def test_branch_update_reads_before_writing_and_never_replaces_protection(
    monkeypatch, read_fails
) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(
            command,
            int(read_fails),
            json.dumps({"checks": [{"context": "custom", "app_id": 42}]}),
            "denied" if read_fails else "",
        )

    monkeypatch.setattr(settings.subprocess, "run", run)
    step = next(
        step for step in settings.steps("owner/repo") if step.merge_status_checks
    )
    error = settings._run(step)
    assert (error is not None) == read_fails
    assert len(calls) == (1 if read_fails else 2)
    if not read_fails:
        assert calls[1][0][3] == "PATCH"
        payload = json.loads(calls[1][1]["input"])
        assert payload["checks"][0] == {"context": "custom", "app_id": 42}
        assert set(payload) == {"strict", "checks"}


@pytest.mark.parametrize(
    ("strict", "missing"), [(True, False), (True, True), (False, False)]
)
def test_check_only_reads_and_reports_drift(monkeypatch, strict, missing) -> None:
    checks = list(settings.REQUIRED_CHECKS)
    if missing:
        checks.pop()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return CompletedProcess(
            command, 0, json.dumps({"strict": strict, "contexts": checks}), ""
        )

    monkeypatch.setattr(settings.subprocess, "run", run)
    assert settings.main(["--check"]) == int(missing or not strict)
    assert len(calls) == 1
    assert "-X" not in calls[0]


def test_apply_reports_any_failed_protection_as_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "_run", lambda step: "denied" if step.merge_status_checks else None
    )
    assert settings.main(["--apply"]) == 1
