"""Verify release contents against an exact Git commit, including published assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "custom_components/mennekes_amtron_rtu/"
ZIP_NAME = "ha-mennekes-amtron.zip"
ASSETS = {ZIP_NAME, "SHA256SUMS", "build-metadata.json", "sbom.spdx.json"}


def run(*args: str) -> bytes:
    """Run fixed local tools without exposing remote error bodies or credentials."""
    result = subprocess.run(args, cwd=ROOT, capture_output=True, check=False)
    if result.returncode:
        raise ValueError(f"{args[0]} failed; check access and the requested reference")
    return result.stdout


def source_files(commit: str) -> dict[str, bytes]:
    """Read regular component files and license notices from the approved tree."""
    files = {}
    listing = run("git", "ls-tree", "-rz", commit, "--", PREFIX, "LICENSE", "NOTICE")
    for record in listing.split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("release source contains a non-regular file")
        name = path.decode().removeprefix(PREFIX)
        files[name] = run("git", "cat-file", "blob", object_id.decode())
    if not {"manifest.json", "LICENSE", "NOTICE"} <= files.keys():
        raise ValueError("release source is incomplete")
    return files


def verify_archive(path: Path, files: dict[str, bytes]) -> None:
    """Reject extra, missing, duplicate, symlink or changed ZIP members."""
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != files.keys():
            raise ValueError("archive file set differs from the approved tree")
        for item in archive.infolist():
            if (stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}
                    or item.file_size != len(files[item.filename])):
                raise ValueError("archive member type or size differs from source")
            if archive.read(item) != files[item.filename]:
                raise ValueError("archive content differs from source")


def remote_commit(repository: str, tag: str) -> str:
    obj = json.loads(run("gh", "api", f"repos/{repository}/git/ref/tags/{tag}"))["object"]
    for _ in range(8):
        if obj.get("type") == "commit":
            return str(obj["sha"])
        if obj.get("type") != "tag" or not re.fullmatch(r"[0-9a-f]{40}", str(obj.get("sha"))):
            break
        obj = json.loads(run("gh", "api", f"repos/{repository}/git/tags/{obj['sha']}"))["object"]
    raise ValueError("release tag does not resolve to a commit")


def verify_release(release: dict[str, Any], tag: str, notes: str, *, draft: bool = False) -> None:
    if release.get("tag_name") != tag or release.get("draft") is not draft or release.get("prerelease"):
        raise ValueError("expected the published stable release")
    if str(release.get("body", "")).replace("\r\n", "\n").strip() != notes.strip():
        raise ValueError("release notes differ from the approved source")
    assets = release.get("assets", [])
    if len(assets) != len(ASSETS) or {a.get("name") for a in assets} != ASSETS:
        raise ValueError("expected one installation ZIP and three verification assets")
    if any(not 0 < a.get("size", 0) <= 25 * 1024**2 for a in assets):
        raise ValueError("invalid release asset size")


def verify_assets(directory: Path, release: dict[str, Any], repository: str,
                  commit: str, version: str, files: dict[str, bytes]) -> None:
    digests = {}
    for asset in release["assets"]:
        name = asset["name"]
        content = (directory / name).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != asset["size"] or asset.get("digest") != f"sha256:{digest}":
            raise ValueError("download differs from GitHub asset size/digest")
        digests[name] = digest
    lines = (directory / "SHA256SUMS").read_text().splitlines()
    if len(lines) != 3:
        raise ValueError("checksum inventory is incomplete")
    sums = dict(line.split(maxsplit=1)[::-1] for line in lines)
    if sums != {name: digest for name, digest in digests.items() if name != "SHA256SUMS"}:
        raise ValueError("release checksums do not match the downloads")
    metadata = json.loads((directory / "build-metadata.json").read_text())
    expected = {"release_tag": f"v{version}", "repository": repository,
                "integration_version": version, "git_commit": commit,
                "zip_entries": len(files), "zip_sha256": digests[ZIP_NAME]}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("build metadata differs from the approved release")
    sbom = json.loads((directory / "sbom.spdx.json").read_text())
    packages = [p for p in sbom.get("packages", []) if p.get("name") == "ha-mennekes-amtron"]
    if (sbom.get("spdxVersion") != "SPDX-2.3" or len(packages) != 1
            or packages[0].get("versionInfo") != version
            or packages[0].get("licenseDeclared") != "Apache-2.0"
            or packages[0].get("checksums") != [{"algorithm": "SHA256", "checksumValue": digests[ZIP_NAME]}]):
        raise ValueError("SBOM does not describe this release archive")
    verify_archive(directory / ZIP_NAME, files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--repo", default="memphi2/ha-mennekes-amtron")
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        parser.error("expected owner/repository")
    try:
        if args.ref == "HEAD" and run("git", "status", "--porcelain").strip():
            raise ValueError("commit changes before verifying HEAD")
        commit = run("git", "rev-parse", "--verify", "--end-of-options", f"{args.ref}^{{commit}}").decode().strip()
        files = source_files(commit)
        manifest = json.loads(files["manifest.json"])
        version = manifest["version"]
        if not re.fullmatch(r"\d+\.\d+\.\d+", version) or manifest["domain"] != "mennekes_amtron_rtu":
            raise ValueError("invalid stable version or integration domain")
        tag = f"v{version}"
        notes = run("git", "show", f"{commit}:.github/release-notes/{tag}.md").decode()
        if not notes.strip():
            raise ValueError("release notes are empty")
        if args.archive:
            verify_archive(args.archive, files)
        if args.published:
            if remote_commit(args.repo, tag) != commit:
                raise ValueError("published tag differs from the approved commit")
            release = json.loads(run("gh", "api", f"repos/{args.repo}/releases/tags/{tag}"))
            verify_release(release, tag, notes)
            with tempfile.TemporaryDirectory(prefix="amtron-release-check-") as directory:
                run("gh", "release", "download", tag, "--repo", args.repo, "--dir", directory,
                    *(arg for name in sorted(ASSETS) for arg in ("--pattern", name)))
                verify_assets(Path(directory), release, args.repo, commit, version, files)
        sys.stdout.write(f"Release {tag} verified against {commit}" + (" including published assets\n" if args.published else "\n"))
        return 0
    except (OSError, ValueError, KeyError, TypeError, BadZipFile):
        sys.stderr.write("Release verification failed; check reference, metadata and asset integrity.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
