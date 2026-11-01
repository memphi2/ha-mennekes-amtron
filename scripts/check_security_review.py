#!/usr/bin/env python3
"""Require a current, clean Codex security receipt before allowing a merge.

Only trusted default-branch code runs this gate. PR contents and bot-comment
bodies are data, never executable code. Missing or changed bot formats block.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from typing import Any

STATUS_CONTEXT = "codex-security-review"
BOT_LOGIN = "chatgpt-codex-connector[bot]"
BOT_ID = 199175422
APP_ID = 1144995
CLEAN_RECEIPT = (
    "Security review completed. No security issues were found in this pull request."
)


def api(path: str, body: dict[str, Any] | None = None) -> Any:
    """Call GitHub through authenticated gh without exposing credentials."""

    command = ["gh", "api", path]
    if body is not None:
        command.extend(["--method", "POST", "--input", "-"])
    result = subprocess.run(
        command,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def pages(path: str) -> list[dict[str, Any]]:
    """Read all result pages, including PRs with more than 100 comments."""

    result: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = api(f"{path}?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Expected a GitHub result list")
        result.extend(batch)
        if len(batch) < 100:
            return result
        page += 1


def trusted(comment: dict[str, Any]) -> bool:
    """Check immutable bot and App IDs as well as the visible login."""

    user = comment.get("user") or {}
    app = comment.get("performed_via_github_app") or {}
    return bool(
        user.get("id") == BOT_ID
        and user.get("login") == BOT_LOGIN
        and user.get("type") == "Bot"
        and app.get("id") == APP_ID
    )


def marker(body: str, name: str) -> dict[str, Any]:
    """Parse exactly one recognized bot-state marker; unknown data stays closed."""

    matches = re.findall(r"<!-- " + re.escape(name) + r":v1 (.*?) -->", body)
    if len(matches) != 1:
        return {}
    try:
        value = json.loads(matches[0])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def evaluate(
    repository: str, number: int, sha: str, comments: list[dict[str, Any]]
) -> tuple[str, str, str | None]:
    """Return a commit status, short explanation and evidence URL."""

    bot_comments = sorted((c for c in comments if trusted(c)), key=lambda c: c["id"])
    summaries = [
        c
        for c in bot_comments
        if "<!-- codex-pull-request-review-summary -->" in c.get("body", "")
    ]
    if not summaries:
        return "pending", "Waiting for Codex security review", None
    summary = summaries[-1]
    state = marker(summary["body"], "codex-review-state").get("security_review")
    metadata = marker(summary["body"], "codex-security-review")
    if not isinstance(state, dict) or state.get("commit_sha") != sha:
        return (
            "pending",
            "Security review is missing or belongs to another commit",
            None,
        )
    if state.get("status") != "completed":
        return "pending", "Security review has not completed", None
    if (
        metadata.get("headSha") != sha
        or metadata.get("status") != "completed"
        or metadata.get("repository") != repository
        or metadata.get("pullRequestNumber") != number
    ):
        return "failure", "Security review evidence is incomplete or inconsistent", None
    if any(
        state.get(key) is not None and state[key] is not False
        for key in ("has_unresolved_findings", "has_any_findings")
    ):
        return (
            "failure",
            "Codex reported security findings; fix and request a new review",
            None,
        )
    receipts = [
        c
        for c in bot_comments
        if "<!-- codex-review-receipt:v1 -->" in c.get("body", "")
        and "Codex Security Review" in c["body"]
    ]
    if not receipts:
        return "failure", "No clean security-review receipt for this commit", None
    receipt = receipts[-1]
    requests = [
        c
        for c in comments
        if c.get("body", "").strip() == "@codex security review"
        and c.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}
    ]
    if requests:
        try:
            completed_at = datetime.fromisoformat(receipt["created_at"])
            requested_at = [datetime.fromisoformat(c["updated_at"]) for c in requests]
            if completed_at.tzinfo is None or any(t.tzinfo is None for t in requested_at):
                raise ValueError("Review timestamps must include a timezone")
        except KeyError, TypeError, ValueError:
            return "pending", "Cannot verify security-review request timestamps", None
        # IDs do not change when an old comment is edited into a request. Use
        # its edit time and the receipt's creation time; editing a receipt later
        # must not renew approval. Equal-second timestamps stay closed.
        if any(t >= completed_at for t in requested_at) or any(
            c["id"] > receipt["id"] for c in requests
        ):
            return "pending", "Waiting for the newly requested security review", None
    reviewed = re.search(
        r"\*\*Reviewed commit:\*\* `([0-9a-f]{10,40})`", receipt["body"]
    )
    if (
        not reviewed
        or not sha.startswith(reviewed[1])
        or CLEAN_RECEIPT not in receipt["body"]
    ):
        return "failure", "No clean security-review receipt for this commit", None
    return (
        "success",
        "Codex security review completed with no reported issues",
        receipt["html_url"],
    )


def check(repository: str, number: int, *, publish: bool) -> bool:
    """Check approval, or publish a success/pending status without job noise.

    Publishing pending is a successful delivery of a blocking status. In
    read-only mode, only an approved review counts as success.
    """

    pull = api(f"repos/{repository}/pulls/{number}")
    if pull["state"] != "open":
        return True
    sha = pull["head"]["sha"]
    if publish:
        # Revoke any previous result before querying evidence. API failures must
        # not leave a stale success behind on a repeated review of the same SHA.
        api(
            f"repos/{repository}/statuses/{sha}",
            {
                "context": STATUS_CONTEXT,
                "state": "pending",
                "description": "Verifying current Codex security review",
            },
        )
    state, description, evidence = evaluate(
        repository, number, sha, pages(f"repos/{repository}/issues/{number}/comments")
    )
    if publish:
        payload = {
            "context": STATUS_CONTEXT,
            "state": state,
            "description": description,
        }
        if evidence:
            payload["target_url"] = evidence
        api(f"repos/{repository}/statuses/{sha}", payload)
    sys.stdout.write(f"PR #{number} {sha}: {state}: {description}\n")
    return state == "success" or (publish and state == "pending")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int)
    target.add_argument("--head-sha")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
        parser.error("Expected owner/repository")
    if args.pr is not None and args.pr <= 0:
        parser.error("Expected a positive PR number")
    if args.head_sha and not re.fullmatch(r"[0-9a-f]{40}", args.head_sha):
        parser.error("Expected a full commit SHA")
    numbers = (
        [args.pr]
        if args.pr
        else [
            p["number"]
            for p in pages(f"repos/{args.repository}/commits/{args.head_sha}/pulls")
            if p["state"] == "open"
            and p["base"]["repo"]["full_name"] == args.repository
        ]
    )
    try:
        results = [
            check(args.repository, number, publish=args.publish) for number in numbers
        ]
    except subprocess.CalledProcessError, ValueError, KeyError, TypeError:
        sys.stderr.write(
            "Cannot verify Codex security review; merge remains blocked.\n"
        )
        return 1
    return int(not all(results))


if __name__ == "__main__":
    sys.exit(main())
