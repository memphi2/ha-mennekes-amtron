"""The merge gate must reject spoofed, stale and incomplete review evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from subprocess import CalledProcessError

import pytest
import yaml

from scripts import check_security_review as gate

REPO = "owner/repo"
SHA = "a" * 40


def evidence():
    identity = {
        "user": {"id": gate.BOT_ID, "login": gate.BOT_LOGIN, "type": "Bot"},
        "performed_via_github_app": {"id": gate.APP_ID},
    }
    state = {
        "security_review": {
            "commit_sha": SHA,
            "status": "completed",
            "has_any_findings": None,
            "has_unresolved_findings": None,
        }
    }
    metadata = {
        "headSha": SHA,
        "status": "completed",
        "repository": REPO,
        "pullRequestNumber": 16,
    }
    return [
        {
            **copy.deepcopy(identity),
            "id": 1,
            "body": "<!-- codex-pull-request-review-summary -->\n"
             f"<!-- codex-review-state:v1 {json.dumps(state)} -->\n"
             f"<!-- codex-security-review:v1 {json.dumps(metadata)} -->",
        },
        {
            **copy.deepcopy(identity),
            "id": 2,
            "created_at": "2026-11-01T19:00:00Z",
            "updated_at": "2026-11-01T19:00:00Z",
            "html_url": "https://github.com/owner/repo/pull/16#issuecomment-2",
            "body": "<!-- codex-review-receipt:v1 -->\n### Codex Security Review\n"
            + gate.CLEAN_RECEIPT
            + f"\n**Reviewed commit:** `{SHA[:10]}`",
        },
    ]


def test_current_clean_review_passes_with_link_to_evidence():
    state, _, url = gate.evaluate(REPO, 16, SHA, evidence())
    assert state == "success"
    assert url.endswith("#issuecomment-2")


@pytest.mark.parametrize(
    "tamper",
    [
        "none",
        "user_id",
        "app_id",
        "login",
        "user_type",
        "missing_app",
        "head",
        "running",
        "failed",
        "metadata",
        "other_pr",
        "other_repo",
        "findings",
        "unresolved",
        "no_receipt",
        "old_receipt",
        "regular_review",
        "unknown_format",
        "duplicate_marker",
        "non_mapping",
        "malformed_json",
        "newer_bad_receipt",
        "no_clean_result",
        "no_commit",
    ],
)
def test_bad_evidence_never_passes(tamper):
    comments = evidence()
    if tamper == "none":
        comments = []
    elif tamper in {"user_id", "app_id", "login", "user_type", "missing_app"}:
        for comment in comments:
            if tamper == "user_id":
                comment["user"]["id"] = 42
            elif tamper == "app_id":
                comment["performed_via_github_app"]["id"] = 42
            elif tamper == "login":
                comment["user"]["login"] = "impostor"
            elif tamper == "user_type":
                comment["user"]["type"] = "User"
            else:
                del comment["performed_via_github_app"]
    elif tamper == "head":
        comments[0]["body"] = comments[0]["body"].replace(SHA, "b" * 40)
    elif tamper in {"running", "failed"}:
        comments[0]["body"] = comments[0]["body"].replace(
            '"completed"', json.dumps(tamper)
        )
    elif tamper == "metadata":
        comments[0]["body"] = comments[0]["body"].split("<!-- codex-security-review:")[
            0
        ]
    elif tamper == "other_pr":
        comments[0]["body"] = comments[0]["body"].replace(
            '"pullRequestNumber": 16', '"pullRequestNumber": 17'
        )
    elif tamper == "other_repo":
        comments[0]["body"] = comments[0]["body"].replace(REPO, "other/repo")
    elif tamper in {"findings", "unresolved"}:
        key = "has_any_findings" if tamper == "findings" else "has_unresolved_findings"
        comments[0]["body"] = comments[0]["body"].replace(
            f'"{key}": null', f'"{key}": true'
        )
    elif tamper == "no_receipt":
        comments.pop()
    elif tamper == "old_receipt":
        comments[1]["body"] = comments[1]["body"].replace(SHA[:10], "b" * 10)
    elif tamper == "regular_review":
        comments[1]["body"] = comments[1]["body"].replace(
            "Codex Security Review", "Codex Review"
        )
    elif tamper == "unknown_format":
        comments[0]["body"] = comments[0]["body"].replace(":v1", ":v2")
    elif tamper == "duplicate_marker":
        comments[0]["body"] += comments[0]["body"]
    elif tamper in {"non_mapping", "malformed_json"}:
        comments[0]["body"] = (
            "<!-- codex-pull-request-review-summary -->\n<!-- codex-review-state:v1 "
            + ("[]" if tamper == "non_mapping" else "{")
            + " -->"
        )
    elif tamper == "newer_bad_receipt":
        newer = copy.deepcopy(comments[1])
        newer["id"] = 3
        newer["body"] = newer["body"].replace(
            gate.CLEAN_RECEIPT, "Security findings require attention"
        )
        comments.append(newer)
    elif tamper == "no_clean_result":
        comments[1]["body"] = comments[1]["body"].replace(
            gate.CLEAN_RECEIPT, "Review complete"
        )
    else:
        comments[1]["body"] = comments[1]["body"].replace(
            "Reviewed commit", "Different commit"
        )
    assert gate.evaluate(REPO, 16, SHA, comments)[0] != "success"


def test_paginated_evidence_is_not_truncated(monkeypatch):
    responses = [[{"id": i} for i in range(100)], [{"id": 100}]]
    monkeypatch.setattr(gate, "api", lambda path: responses.pop(0))
    assert len(gate.pages("comments")) == 101


def test_an_old_clean_receipt_cannot_satisfy_a_new_manual_review():
    comments = evidence()
    comments.append({
        "id": 3, "body": "@codex security review", "author_association": "OWNER",
        "updated_at": "2026-11-01T19:01:00Z",
    })
    assert gate.evaluate(REPO, 16, SHA, comments)[0] == "pending"
    fresh = copy.deepcopy(comments[1])
    fresh["id"] = 4
    fresh["created_at"] = "2026-11-01T19:02:00Z"
    comments.append(fresh)
    assert gate.evaluate(REPO, 16, SHA, comments)[0] == "success"


def test_editing_an_old_comment_into_a_request_requires_a_fresh_receipt():
    comments = evidence()
    comments.append({
        "id": 0, "body": "@codex security review", "author_association": "OWNER",
        "created_at": "2026-11-01T18:00:00Z",
        "updated_at": "2026-11-01T19:01:00Z",
    })
    # A later edit to the old receipt is not a new completed review.
    comments[1]["updated_at"] = "2026-11-01T19:02:00Z"
    assert gate.evaluate(REPO, 16, SHA, comments)[0] == "pending"
    fresh = copy.deepcopy(comments[1])
    fresh.update(id=3, created_at="2026-11-01T19:03:00Z")
    comments.append(fresh)
    assert gate.evaluate(REPO, 16, SHA, comments)[0] == "success"


@pytest.mark.parametrize(
    ("association", "updated_at", "expected"),
    [
        ("OWNER", "2026-11-01T18:59:59Z", "success"),
        ("MEMBER", "2026-11-01T19:00:00Z", "pending"),
        ("COLLABORATOR", "2026-11-01T20:01:00+01:00", "pending"),
        ("NONE", "2026-11-01T19:01:00Z", "success"),
    ],
)
def test_review_request_edit_order_and_authorization(association, updated_at, expected):
    comments = evidence()
    comments.append({
        "id": 0, "body": "@codex security review", "author_association": association,
        "updated_at": updated_at,
    })
    assert gate.evaluate(REPO, 16, SHA, comments)[0] == expected


@pytest.mark.parametrize("target", ["receipt", "request"])
@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-11-01T18:59:00"])
def test_unverifiable_review_timestamps_never_approve(target, timestamp):
    comments = evidence()
    request = {
        "id": 0, "body": "@codex security review", "author_association": "OWNER",
        "updated_at": "2026-11-01T18:59:00Z",
    }
    comments.append(request)
    comment, key = (comments[1], "created_at") if target == "receipt" else (request, "updated_at")
    if timestamp is None:
        del comment[key]
    else:
        comment[key] = timestamp
    assert gate.evaluate(REPO, 16, SHA, comments)[0] == "pending"


def test_read_only_check_never_publishes(monkeypatch):
    calls = []

    def api(path, body=None):
        calls.append((path, body))
        if "/pulls/" in path:
            return {"state": "open", "head": {"sha": SHA}}
        return evidence()

    monkeypatch.setattr(gate, "api", api)
    assert gate.check(REPO, 16, publish=False)
    assert all(body is None for _, body in calls)


def test_publish_clears_old_success_before_reading_evidence(monkeypatch):
    writes = []

    def api(path, body=None):
        if "/pulls/" in path:
            return {"state": "open", "head": {"sha": SHA}}
        if body:
            writes.append(body)
            return {}
        raise CalledProcessError(1, "gh")

    monkeypatch.setattr(gate, "api", api)
    assert gate.main(["--repository", REPO, "--pr", "16", "--publish"]) == 1
    assert writes == [
        {
            "context": gate.STATUS_CONTEXT,
            "state": "pending",
            "description": "Verifying current Codex security review",
        }
    ]


def test_workflow_never_executes_pr_code_or_downloads_artifacts():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/security-review.yml"
    workflow = yaml.safe_load(path.read_text())
    assert set(workflow[True]) == {"issue_comment", "workflow_run", "workflow_dispatch"}
    assert workflow["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "statuses": "write",
    }
    steps = workflow["jobs"]["verify"]["steps"]
    condition = workflow["jobs"]["verify"]["if"]
    assert "github.event.comment.author_association" in condition
    assert 'fromJSON(\'["OWNER", "MEMBER", "COLLABORATOR"]\')' in condition
    assert len(steps) == 3
    assert steps[0]["with"] == {
        "ref": "${{ github.event.repository.default_branch }}",
        "persist-credentials": False,
    }
    assert steps[1]["with"]["python-version"] == "3.14"
    assert "${{" not in steps[2]["run"]
    assert "download-artifact" not in path.read_text()


@pytest.mark.parametrize("valid", [True, False])
def test_published_status_uses_verified_head_and_result(monkeypatch, valid):
    writes = []

    def api(path, body=None):
        if "/pulls/" in path:
            return {"state": "open", "head": {"sha": SHA}}
        if body:
            writes.append((path, body))
            return {}
        return evidence() if valid else []

    monkeypatch.setattr(gate, "api", api)
    assert gate.check(REPO, 16, publish=True)
    assert all(path == f"repos/{REPO}/statuses/{SHA}" for path, _ in writes)
    assert [body["state"] for _, body in writes] == ["pending", "success" if valid else "pending"]
    assert writes[-1][1]["context"] == gate.STATUS_CONTEXT


@pytest.mark.parametrize(
    ("state", "publish", "expected"),
    [("pending", True, 0), ("pending", False, 1),
     ("failure", True, 1), ("failure", False, 1)],
)
def test_pending_publication_is_quiet_but_never_an_approval(monkeypatch, state, publish, expected):
    writes = []

    def api(path, body=None):
        if "/pulls/" in path:
            return {"state": "open", "head": {"sha": SHA}}
        if body is not None:
            writes.append(body["state"])
            return {}
        return []

    monkeypatch.setattr(gate, "api", api)
    monkeypatch.setattr(gate, "evaluate", lambda *_args: (state, "test result", None))
    args = ["--repository", REPO, "--pr", "16"]
    if publish:
        args.append("--publish")
    assert gate.main(args) == expected
    assert writes == (["pending", state] if publish else [])


def test_required_context_cannot_drift_from_verifier():
    from scripts.apply_repo_settings import REQUIRED_CHECKS

    assert gate.STATUS_CONTEXT in REQUIRED_CHECKS
