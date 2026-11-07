"""Git's index, not ignore rules, determines whether caches will be published."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import check_repo
import pytest

GIT = shutil.which("git")
assert GIT is not None


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a real index so a forced add exercises the original failure."""
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)
    return tmp_path


@pytest.mark.parametrize("directory", check_repo.FORBIDDEN_TRACKED_DIRS)
def test_force_added_ignored_artifacts_are_rejected(
    repository: Path, directory: str
) -> None:
    (repository / ".gitignore").write_text(f"{directory}/\n")
    artifacts = repository / "nested folder" / directory
    artifacts.mkdir(parents=True)
    for name in ("one.bin", "two.bin"):
        (artifacts / name).write_bytes(b"cache")
    assert check_repo.check_tracked_runtime_artifacts() == []

    subprocess.run(
        [GIT, "add", "--force", "--", str(artifacts)],
        cwd=repository,
        check=True,
    )
    assert check_repo.check_tracked_runtime_artifacts() == [
        f"runtime artifact directory is tracked: nested folder/{directory}"
    ]

    # A staged artifact remains part of the next commit even if deleted only
    # from the working tree. Filesystem traversal cannot detect this case.
    for artifact in artifacts.iterdir():
        artifact.unlink()
    artifacts.rmdir()
    assert check_repo.check_tracked_runtime_artifacts()


def test_regular_source_and_untracked_caches_are_allowed(repository: Path) -> None:
    source = repository / "source.py"
    source.write_text("value = 1\n")
    subprocess.run([GIT, "add", "source.py"], cwd=repository, check=True)
    cache = repository / "__pycache__"
    cache.mkdir()
    (cache / "source.pyc").write_bytes(b"cache")
    assert check_repo.check_tracked_runtime_artifacts() == []


def test_artifact_check_fails_closed_without_a_git_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)
    assert check_repo.check_tracked_runtime_artifacts() == [
        "cannot inspect the Git index for tracked runtime artifacts"
    ]
