"""Plan app publication without changing an existing image or app catalog."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from packaging.version import Version
from publish_service_catalog import api, verify_existing_catalog
from release_check import remote_commit
from release_version import (
    catalog_branch,
    catalog_branches,
    is_prerelease,
    validate_version,
)
from service_image import DIGEST, existing_verified_image, release_info
from service_inputs import ROOT, fingerprint, git, minimum_service_version

SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class ServicePlan:
    """The immutable app identity and only the publication work still needed."""

    info: dict[str, str]
    inputs_sha256: str
    build_required: bool
    catalog_required: bool
    digest: str = ""


def normalize_catalog(value: dict[str, Any], repository: str, branch: str) -> dict[str, str]:
    """Read old stable catalogs while requiring their original source identity."""
    required = {"image", "version", "commit", "channel", "repository", "digest"}
    if not required <= value.keys() or value.keys() - required - {"source_tag", "catalog_channel"}:
        raise ValueError("invalid app catalog metadata fields")
    if not all(isinstance(item, str) for item in value.values()):
        raise ValueError("invalid app catalog metadata values")
    version = validate_version(value["version"])
    source_tag = value.get("source_tag", f"v{version}")
    if not source_tag.startswith("v"):
        raise ValueError("invalid original app source tag")
    validate_version(source_tag[1:])
    if (
        value["repository"] != repository
        or value["image"] != f"ghcr.io/{repository}-service"
        or value["channel"] != catalog_branch(version)
        or value.get("catalog_channel", value["channel"]) != branch
        or branch not in catalog_branches(version)
        or not SHA.fullmatch(value["commit"])
        or not DIGEST.fullmatch(value["digest"])
    ):
        raise ValueError("app catalog identity differs")
    return {**value, "source_tag": source_tag, "catalog_channel": branch}


def read_catalog(repository: str, branch: str) -> dict[str, str] | None:
    """Pin the metadata read to one branch commit, never two mutable reads."""
    ref = api(repository, f"git/ref/heads/{branch}", missing_ok=True)
    if ref is None:
        return None
    reference = ref.get("object")
    if not isinstance(reference, dict):
        raise ValueError("invalid app catalog reference")
    commit = reference.get("sha", "")
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise ValueError("invalid app catalog commit")
    result = api(repository, f"contents/release.json?ref={commit}")
    if result is None or result.get("encoding") != "base64" or not isinstance(result.get("content"), str):
        raise ValueError("app catalog release metadata is missing")
    value = json.loads(base64.b64decode(result["content"], validate=False))
    if not isinstance(value, dict):
        raise ValueError("invalid app catalog release metadata")
    return normalize_catalog(value, repository, branch)


def decide(
    info: dict[str, str], inputs: str, minimum: str,
    previous: dict[str, str] | None, previous_inputs: str | None,
) -> bool:
    """Return whether a new app publication is needed, without registry access."""
    version = Version(info["version"])
    if version < Version(minimum):
        raise ValueError("selected service version is below the integration minimum")
    if previous is not None:
        old = Version(previous["version"])
        if version < old:
            raise ValueError("refusing to downgrade the published app")
        if version == old:
            if inputs != previous_inputs:
                raise ValueError("app inputs changed; increase the independent service version")
            return False
        if inputs == previous_inputs and not (
            old.is_prerelease and not version.is_prerelease
            and old.base_version == version.base_version
        ):
            raise ValueError("app version changed without relevant app changes")
    # A beta integration may reuse a stable app, but must not publish changed
    # app code directly to the stable catalog (nor the reverse).
    if is_prerelease(info["source_tag"]) != version.is_prerelease:
        raise ValueError("new app and integration releases must use the same stable/preview channel")
    return True


def _source_info(previous: dict[str, str]) -> dict[str, str]:
    return {key: previous[key] for key in (
        "repository", "image", "version", "commit", "source_tag", "channel"
    )}


def _catalog_resume_needed(info: dict[str, str], digest: str) -> bool:
    """Complete this original release after only one catalog was advanced."""
    pending = False
    for branch in catalog_branches(info["version"]):
        previous = read_catalog(info["repository"], branch)
        if previous is None or Version(previous["version"]) < Version(info["version"]):
            pending = True
            continue
        if Version(previous["version"]) > Version(info["version"]):
            if branch == "apps-beta" and info["channel"] == "apps":
                continue
            raise ValueError("refusing to downgrade an app catalog during resume")
        if _source_info(previous) != info or previous["digest"] != digest:
            raise ValueError("same-version app catalog differs during resume")
        verify_existing_catalog(info, digest, branch)
    return pending


def plan(repository: str, tag: str) -> ServicePlan:
    """Verify source, minimum version and existing provenance before any build."""
    info = release_info(repository, tag)
    if git(ROOT, "status", "--porcelain").strip():
        raise ValueError("commit all source changes before planning a release")
    if remote_commit(repository, tag) != info["commit"]:
        raise ValueError("release tag changed before app planning")
    inputs = fingerprint(info["commit"])
    previous = read_catalog(repository, info["channel"])
    if previous is None:
        # First stable promotion may follow a preview-only catalog. A first
        # preview may likewise reuse the service already in the stable catalog.
        alternate = "apps-beta" if info["channel"] == "apps" else "apps"
        previous = read_catalog(repository, alternate)
    previous_inputs = None
    if previous is not None:
        if remote_commit(repository, previous["source_tag"]) != previous["commit"]:
            raise ValueError("published app source tag no longer matches its catalog")
        git(ROOT, "merge-base", "--is-ancestor", previous["commit"], info["commit"])
        previous_inputs = fingerprint(previous["commit"])
    publish = decide(info, inputs, minimum_service_version(), previous, previous_inputs)
    if not publish:
        if previous is None:
            raise ValueError("cannot reuse a service without a prior catalog identity")
        original = _source_info(previous)
        digest = existing_verified_image(original)
        if digest is None or digest != previous["digest"]:
            raise ValueError("published app image is missing or differs from its catalog")
        resume = original == info and _catalog_resume_needed(original, digest)
        if original != info:
            verify_existing_catalog(original, digest, previous["catalog_channel"])
        return ServicePlan(original, inputs, False, resume, digest)
    digest = existing_verified_image(info)
    return ServicePlan(info, inputs, digest is None, True, digest or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="memphi2/ha-mennekes-amtron")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        result = plan(args.repository, args.tag)
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a") as stream:
                stream.write(
                    f"build_required={str(result.build_required).lower()}\n"
                    f"catalog_required={str(result.catalog_required).lower()}\n"
                    f"digest={result.digest}\n"
                )
        sys.stdout.write(json.dumps(asdict(result), sort_keys=True) + "\n")
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        sys.stderr.write(f"Service release planning stopped: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
