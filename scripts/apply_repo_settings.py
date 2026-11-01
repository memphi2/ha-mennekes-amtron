#!/usr/bin/env python3
"""Apply the repository's GitHub settings and branch protection.

The settings that protect a repository do not live in the repository, so
they are easy to forget and impossible to review in a diff. This script
declares them instead, prints what it would change, and applies them with
``--apply``. It is idempotent.

Existing branch protection is required. Only the required-checks endpoint is
updated, preserving review rules, administrator enforcement and restrictions.
API failures stop the run instead of being reported as successful protection.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOSITORY = "memphi2/ha-mennekes-amtron"

# Status checks that have to pass before main can move. The names are the CI
# job names; scripts/check_repo.py verifies that they still exist in
# .github/workflows/validate.yml.
REQUIRED_CI_CHECKS = (
    "dependabot",
    "hacs",
    "hassfest",
    "validate (min-ha)",
    "validate (current-ha)",
    "validate (preview-ha)",
)
REQUIRED_CHECKS = (*REQUIRED_CI_CHECKS, "codex-security-review")

REPOSITORY_SETTINGS = {
    "has_issues": True,
    "has_wiki": False,
    "has_projects": False,
    "allow_squash_merge": True,
    "allow_merge_commit": True,
    "allow_rebase_merge": True,
    "allow_auto_merge": False,
    "delete_branch_on_merge": True,
}


@dataclass(frozen=True)
class Step:
    """One GitHub API call that enforces part of the configuration."""

    name: str
    method: str
    path: str
    body: object | None = None
    merge_status_checks: bool = False


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
            "Dependabot security updates",
            "PUT",
            f"repos/{repository}/automated-security-fixes",
        ),
        Step(
            "Private vulnerability reporting",
            "PUT",
            f"repos/{repository}/private-vulnerability-reporting",
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
        ),
        Step(
            "Branch protection for main",
            "PATCH",
            f"repos/{repository}/branches/main/protection/required_status_checks",
            merge_status_checks=True,
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read live required checks and report drift; change nothing.",
    )
    args = parser.parse_args(argv)
    if args.check:
        if args.apply:
            parser.error("--check and --apply are mutually exclusive")
        return _check_required_checks(args.repository)

    for step in steps(args.repository):
        if not args.apply:
            _write(f"would apply: {step.name}")
            continue
        error = _run(step)
        if error is None:
            _write(f"applied: {step.name}")
        else:
            sys.stderr.write(f"FAIL: {step.name}: {error}\n")
            return 1

    if not args.apply:
        _write("\nNothing was sent. Re-run with --apply.")
        return 0
    return 0


def _run(step: Step) -> str | None:
    body = step.body
    if step.merge_status_checks:
        current, error = _read_required_checks(step.path)
        if error is not None:
            return error
        body = merged_status_checks(current)
    command = ["gh", "api", "-X", step.method, step.path]
    stdin = None
    if body is not None:
        command.extend(["--input", "-"])
        stdin = json.dumps(body)
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


def merged_status_checks(current: dict[str, Any]) -> dict[str, Any]:
    """Add required contexts without losing custom checks or their App IDs."""

    checks = [dict(check) for check in current.get("checks", [])]
    known = {check["context"] for check in checks}
    for context in [*current.get("contexts", []), *REQUIRED_CHECKS]:
        if context not in known:
            check = {"context": context}
            if context == "codex-security-review":
                # Allow both the trusted Actions workflow and an authenticated
                # maintainer running the verifier during initial rollout.
                check["app_id"] = -1
            checks.append(check)
            known.add(context)
    return {"strict": True, "checks": checks}


def _read_required_checks(path: str) -> tuple[dict[str, Any], str | None]:
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, check=False, text=True
    )
    if result.returncode:
        return {}, _message(
            result.stdout
        ) or result.stderr.strip() or "Cannot read existing branch protection"
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        return {}, "Invalid branch-protection response"
    return data, None


def _check_required_checks(repository: str) -> int:
    current, error = _read_required_checks(
        f"repos/{repository}/branches/main/protection/required_status_checks"
    )
    if error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    known = set(current.get("contexts", [])) | {
        check["context"] for check in current.get("checks", [])
    }
    missing = sorted(set(REQUIRED_CHECKS) - known)
    if missing or not current.get("strict"):
        _write(f"Drift: missing checks={missing}, strict={current.get('strict')}")
        return 1
    _write("Required checks match the repository policy")
    return 0


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
