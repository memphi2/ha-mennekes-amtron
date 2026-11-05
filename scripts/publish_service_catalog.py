"""Advance a generated app catalog only after the image and release are available."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from typing import Any

import yaml
from packaging.version import Version
from release_check import remote_commit, run
from release_version import catalog_branch, catalog_branches
from service_image import ROOT, app_dockerfile, release_info, verify_public


def api(
    repository: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    *,
    missing_ok: bool = False,
) -> dict[str, Any] | None:
    command = ["gh", "api", f"repos/{repository}/{endpoint}"]
    if payload is not None:
        command.extend(("--method", "POST", "--input", "-"))
    result = subprocess.run(
        command,
        input=json.dumps(payload).encode() if payload is not None else None,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        if missing_ok and "(HTTP 404)" in result.stderr.decode():
            return None
        raise ValueError("catalog API request failed; check repository access")
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError("invalid catalog API response")
    return data


def _release_metadata(
    info: dict[str, str], digest: str, branch: str
) -> dict[str, str]:
    """Keep the release's provenance channel distinct from its catalog branch."""
    if info["channel"] != catalog_branch(info["version"]):
        raise ValueError("catalog channel differs from the release version")
    if branch not in catalog_branches(info["version"]):
        raise ValueError("release is not allowed in this catalog channel")
    return {**info, "digest": digest, "catalog_channel": branch}


def catalog_files(
    info: dict[str, str], digest: str, *, branch: str | None = None
) -> dict[str, bytes]:
    """Publish an allowlist of metadata and documentation, never development files."""
    branch = branch or info["channel"]
    metadata = _release_metadata(info, digest, branch)
    prefix = "mennekes_amtron_modbus/"
    app = ROOT / "modbus_service"
    files = {
        prefix + name: (app / name).read_bytes()
        for name in ("config.yaml", "README.md", "DOCS.md", "CHANGELOG.md")
    }
    config = yaml.safe_load(files[prefix + "config.yaml"])
    if "image" in config:
        raise ValueError("app config must use the digest-pinned Dockerfile")
    files[prefix + "Dockerfile"] = app_dockerfile(info, digest)
    for translation in sorted((app / "translations").glob("*.yaml")):
        files[f"{prefix}translations/{translation.name}"] = translation.read_bytes()
    for name in ("LICENSE", "NOTICE"):
        files[name] = (ROOT / name).read_bytes()
    for name in ("icon.png", "logo.png"):
        files[prefix + name] = (
            ROOT / "custom_components/mennekes_amtron_rtu/brand/icon.png"
        ).read_bytes()
    title = "Mennekes Amtron" + (" beta" if branch == "apps-beta" else "")
    url = f"https://github.com/{info['repository']}#{branch}"
    files["repository.yaml"] = yaml.safe_dump({"name": title, "url": url}).encode()
    files["README.md"] = (
        f"# {title}\n\nInstall the Modbus service from `{url}`.\n\n"
        f"Release: [{info['version']}](https://github.com/{info['repository']}/releases/tag/v{info['version']}).\n"
        f"\nStandalone container reference for this release:\n\n```text\n{info['image']}@{digest}\n```\n\n"
        "This branch is generated after publication; source changes belong on main.\n"
    ).encode()
    files["release.json"] = (
        json.dumps(metadata, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def check_forward(
    previous: dict[str, Any], info: dict[str, str], digest: str,
    *, branch: str | None = None,
) -> bool:
    """Refuse downgrades and any changed content under an existing version."""
    branch = branch or info["channel"]
    metadata = _release_metadata(info, digest, branch)
    # Catalogs predating stable promotion used one channel field for both roles.
    previous = {"catalog_channel": previous.get("channel"), **previous}
    if (
        previous.get("catalog_channel") != branch
        or previous.get("channel") != catalog_branch(previous["version"])
        or branch not in catalog_branches(previous["version"])
        or previous.get("repository") != info["repository"]
    ):
        raise ValueError("existing catalog identity differs")
    old, new = Version(previous["version"]), Version(info["version"])
    if old > new:
        # A maintenance stable release must not replace an already newer beta
        # (or stable release) in the preview catalog. Its own channel still advances.
        if branch == "apps-beta" and info["channel"] == "apps":
            return False
        raise ValueError("refusing to downgrade an app catalog")
    if old == new:
        if previous != metadata:
            raise ValueError("existing catalog release differs; refusing replacement")
        return False
    return True


def publish(info: dict[str, str], digest: str) -> None:
    repository = info["repository"]
    if remote_commit(repository, f"v{info['version']}") != info["commit"]:
        raise ValueError("release tag changed before catalog publication")
    actual = (
        run(
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            f"{info['image']}:{info['version']}",
            "--format",
            "{{.Manifest.Digest}}",
        )
        .decode()
        .strip()
    )
    if actual != digest:
        raise ValueError("release image tag changed before catalog publication")
    verify_public(info, digest)
    updates: list[tuple[str, dict[str, bytes], str | None]] = []
    # Inspect every target before the first mutation. An invalid identity or a
    # downgrade must not leave the other catalog partially advanced.
    for branch in catalog_branches(info["version"]):
        files = catalog_files(info, digest, branch=branch)
        current = api(repository, f"git/ref/heads/{branch}", missing_ok=True)
        parent = current["object"]["sha"] if current is not None else None
        if parent is not None:
            contents = api(repository, f"contents/release.json?ref={parent}")
            if contents is None:
                raise ValueError("existing catalog has no release identity")
            previous = json.loads(base64.b64decode(contents["content"]))
            if not isinstance(previous, dict):
                raise ValueError("existing catalog has an invalid release identity")
            if not check_forward(previous, info, digest, branch=branch):
                continue
        updates.append((branch, files, parent))
    for branch, files, parent in updates:
        _publish_catalog(info, branch, files, parent)


def _publish_catalog(
    info: dict[str, str], branch: str, files: dict[str, bytes], parent: str | None
) -> None:
    """Advance one reviewed target; completed targets are skipped on a rerun."""
    repository = info["repository"]
    tree_items = []
    for path, content in sorted(files.items()):
        item = {"path": path, "mode": "100644", "type": "blob"}
        if path.endswith(".png"):
            blob = api(
                repository,
                "git/blobs",
                {"encoding": "base64", "content": base64.b64encode(content).decode()},
            )
            if blob is None:
                raise ValueError("catalog image blob was not created")
            item["sha"] = blob["sha"]
        else:
            item["content"] = content.decode()
        tree_items.append(item)
    tree = api(repository, "git/trees", {"tree": tree_items})
    if tree is None:
        raise ValueError("catalog tree was not created")
    commit = api(
        repository,
        "git/commits",
        {
            "message": f"Publish Modbus service {info['version']}",
            "tree": tree["sha"],
            "parents": [parent] if parent is not None else [],
        },
    )
    if commit is None:
        raise ValueError("catalog commit was not created")
    if parent is None:
        api(
            repository,
            "git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
    else:
        # Update explicitly without force: a racing publication must fail rather
        # than overwrite an intervening catalog. No source-branch PR is created.
        result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{repository}/git/refs/heads/{branch}",
                "--input",
                "-",
            ],
            input=json.dumps({"sha": commit["sha"], "force": False}).encode(),
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise ValueError("catalog changed concurrently; refusing a forced update")
    updated = api(repository, f"git/ref/heads/{branch}")
    if updated is None or updated["object"]["sha"] != commit["sha"]:
        raise ValueError("catalog publication was not confirmed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="memphi2/ha-mennekes-amtron")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--digest", required=True)
    args = parser.parse_args()
    try:
        info = release_info(args.repository, args.tag)
        publish(info, args.digest)
    except (ValueError, OSError, KeyError, TypeError) as error:
        sys.stderr.write(f"Catalog publication stopped: {error}\n")
        return 1
    channels = ", ".join(catalog_branches(info["version"]))
    sys.stdout.write(f"Catalogs verified or advanced ({channels}) for {info['version']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
