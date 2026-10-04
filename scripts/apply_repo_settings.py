#!/usr/bin/env python3
"""Apply the repository's GitHub settings and branch protection.

The settings that protect a repository do not live in the repository, so
they are easy to forget and impossible to review in a diff. This script
declares them instead, prints what it would change, and applies them with
``--apply``. It is idempotent.

Some of it needs a public repository or a paid plan. Those steps are reported
as unavailable rather than failing the run, so the same command can be used
again once the repository is published.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOSITORY = "memphi2/ha-mennekes-amtron"

# Status checks that have to pass before main can move. The names are the CI
# job names; scripts/check_repo.py verifies that they still exist in
# .github/workflows/validate.yml.
REQUIRED_CHECKS = (
    "dependabot",
    "hacs",
    "hassfest",
    "validate (min-ha)",
    "validate (current-ha)",
)

REPOSITORY_SETTINGS = {
    "has_issues": True,
    "has_wiki": False,
    "has_projects": False,
    "allow_squash_merge": True,
    "allow_merge_commit": True,
    "allow_rebase_merge": True,
    "allow_auto_merge": False,
    "delete_branch_on_merge": False,
}

BRANCH_PROTECTION = {
    "required_status_checks": {
        "strict": True,
        "contexts": list(REQUIRED_CHECKS),
    },
    "enforce_admins": False,
    "required_pull_request_reviews": None,
    "restrictions": None,
    "required_linear_history": False,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "block_creations": False,
    "required_conversation_resolution": False,
    "lock_branch": False,
    "allow_fork_syncing": False,
}


@dataclass(frozen=True)
class Step:
    """One GitHub API call that enforces part of the configuration."""

    name: str
    method: str
    path: str
    body: object | None = None
    fields: tuple[str, ...] = field(default_factory=tuple)
    needs_public_or_pro: bool = False


def steps(repository: str) -> list[Step]:
    """Return every configuration step, in the order it should be applied."""

    return [
        Step(
            "Repository settings",
            "PATCH",
            f"repos/{repository}",
            REPOSITORY_SETTINGS,
        ),
        Step(
            "Dependabot vulnerability alerts",
            "PUT",
            f"repos/{repository}/vulnerability-alerts",
        ),
        Step(
            "Secret scanning and push protection",
            "PATCH",
            f"repos/{repository}",
            {
                "security_and_analysis": {
                    "secret_scanning": {"status": "enabled"},
                    "secret_scanning_push_protection": {"status": "enabled"},
                }
            },
            needs_public_or_pro=True,
        ),
        Step(
            "Branch protection for main",
            "PUT",
            f"repos/{repository}/branches/main/protection",
            BRANCH_PROTECTION,
            needs_public_or_pro=True,
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    """Print or apply the repository configuration."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually change the repository. Without it, nothing is sent.",
    )
    args = parser.parse_args(argv)

    unavailable: list[str] = []
    for step in steps(args.repository):
        if not args.apply:
            _write(f"would apply: {step.name}")
            continue
        error = _run(step)
        if error is None:
            _write(f"applied: {step.name}")
        elif step.needs_public_or_pro:
            unavailable.append(f"{step.name}: {error}")
            _write(f"unavailable: {step.name}")
        else:
            sys.stderr.write(f"FAIL: {step.name}: {error}\n")
            return 1

    if not args.apply:
        _write("\nNothing was sent. Re-run with --apply.")
        return 0
    if unavailable:
        _write("\nNot applied, needs a public repository or a paid plan:")
        for entry in unavailable:
            _write(f"  {entry}")
    return 0


def _run(step: Step) -> str | None:
    command = ["gh", "api", "-X", step.method, step.path]
    stdin = None
    if step.body is not None:
        command.extend(["--input", "-"])
        stdin = json.dumps(step.body)
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        input=stdin,
        text=True,
    )
    if result.returncode == 0:
        return None
    return _message(result.stdout) or result.stderr.strip() or "failed"


def _message(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return ""
    message = payload.get("message") if isinstance(payload, dict) else None
    return str(message) if message else ""


def _write(line: str) -> None:
    sys.stdout.write(f"{line}\n")


if __name__ == "__main__":
    sys.exit(main())
