"""Publish a verified draft once; existing release assets are never replaced."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from release_check import (
    ASSETS,
    remote_commit,
    run,
    source_files,
    verify_assets,
    verify_release,
)
from release_version import VERSION_PATTERN, is_prerelease


def find_release(repository: str, tag: str) -> dict[str, Any] | None:
    """Distinguish absence from failed authentication or API/network errors."""
    pages = json.loads(run("gh", "api", "--paginate", "--slurp",
                          f"repos/{repository}/releases?per_page=100"))
    matches = [release for page in pages for release in page if release.get("tag_name") == tag]
    if len(matches) > 1:
        raise ValueError("multiple releases for the tag")
    return matches[0] if matches else None


def publish(repository: str, tag: str, directory: Path) -> None:
    """Validate source and downloads before making a draft public."""
    commit = run("git", "rev-parse", "HEAD").decode().strip()
    if remote_commit(repository, tag) != commit:
        raise ValueError("remote tag does not match this checkout")
    files = source_files(commit)
    version = json.loads(files["manifest.json"])["version"]
    if tag != f"v{version}":
        raise ValueError("tag does not match the source version")
    notes = run("git", "show", f"{commit}:.github/release-notes/{tag}.md").decode()
    assets = [{"name": name, "size": (directory / name).stat().st_size,
               "digest": f"sha256:{hashlib.sha256((directory / name).read_bytes()).hexdigest()}"}
              for name in sorted(ASSETS)]
    prerelease = is_prerelease(tag)
    candidate = {"tag_name": tag, "draft": True, "prerelease": prerelease,
                 "body": notes, "assets": assets}
    verify_release(candidate, tag, notes, draft=True)
    verify_assets(directory, candidate, repository, commit, version, files)
    release = find_release(repository, tag)
    if release is None:
        with tempfile.TemporaryDirectory(prefix="amtron-release-notes-") as temporary:
            notes_path = Path(temporary) / "notes.md"
            notes_path.write_text(notes)
            run("gh", "release", "create", tag, "--repo", repository, "--draft",
                "--verify-tag", "--title", tag, "--notes-file", str(notes_path),
                *(("--prerelease", "--latest=false") if prerelease else ()),
                *(str(directory / name) for name in sorted(ASSETS)))
        release = find_release(repository, tag)
    if release is None:
        raise ValueError("draft creation was not confirmed")
    draft = release.get("draft") is True
    verify_release(release, tag, notes, draft=draft)
    with tempfile.TemporaryDirectory(prefix="amtron-release-download-") as temporary:
        run("gh", "release", "download", tag, "--repo", repository, "--dir", temporary,
            *(arg for name in sorted(ASSETS) for arg in ("--pattern", name)))
        downloaded = Path(temporary)
        verify_assets(downloaded, release, repository, commit, version, files)
        if any((downloaded / name).read_bytes() != (directory / name).read_bytes() for name in ASSETS):
            raise ValueError("existing assets differ; refusing to replace them")
    if draft:
        # Recheck the tag after network operations, immediately before publishing.
        if remote_commit(repository, tag) != commit:
            raise ValueError("tag changed while verifying the draft")
        run("gh", "release", "edit", tag, "--repo", repository, "--draft=false",
            *(("--latest=false",) if prerelease else ()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--directory", type=Path, default=Path(".release"))
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo) or not re.fullmatch(f"v{VERSION_PATTERN}", args.tag):
        parser.error("expected owner/repository and vX.Y.Z, vX.Y.ZbN or vX.Y.ZrcN tag")
    try:
        publish(args.repo, args.tag, args.directory)
    except (OSError, ValueError, KeyError, TypeError):
        sys.stderr.write("Release publication stopped: check source, access and asset integrity. Existing assets were not replaced.\n")
        return 1
    sys.stdout.write(f"Release {args.tag} verified; existing assets preserved.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
