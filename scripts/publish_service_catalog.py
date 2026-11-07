"""Advance a generated app catalog only after the image and release are available."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from typing import Any

import yaml
from packaging.version import Version
from release_check import remote_commit, run
from release_version import catalog_branch, catalog_branches
from service_image import ROOT, app_dockerfile, existing_verified_image, release_info


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
    info: dict[str, str], digest: str, *, branch: str | None = None,
    source_ref: str | None = None,
) -> dict[str, bytes]:
    """Publish an allowlist of metadata and documentation, never development files."""
    branch = branch or info["channel"]
    metadata = _release_metadata(info, digest, branch)
    prefix = "mennekes_amtron_modbus/"
    app = ROOT / "modbus_service"

    def read(path: str) -> bytes:
        if source_ref is not None:
            return run("git", "show", f"{source_ref}:{path}")
        return (ROOT / path).read_bytes()

    files = {
        prefix + name: read(f"modbus_service/{name}")
        for name in ("config.yaml", "README.md", "DOCS.md", "CHANGELOG.md", "apparmor.txt")
    }
    config = yaml.safe_load(files[prefix + "config.yaml"])
    if "image" in config:
        raise ValueError("app config must use the digest-pinned Dockerfile")
    files[prefix + "Dockerfile"] = app_dockerfile(info, digest)
    translations = (
        run("git", "ls-tree", "-rz", "--name-only", source_ref, "--", "modbus_service/translations").decode().split("\0")
        if source_ref is not None
        else [path.relative_to(ROOT).as_posix() for path in sorted((app / "translations").glob("*.yaml"))]
    )
    for translation in translations:
        if translation.endswith(".yaml"):
            files[prefix + translation.removeprefix("modbus_service/")] = read(translation)
    for name in ("LICENSE", "NOTICE"):
        files[name] = read(name)
    for name in ("icon.png", "logo.png"):
        files[prefix + name] = read("custom_components/mennekes_amtron_rtu/brand/icon.png")
    title = "Mennekes Amtron" + (" beta" if branch == "apps-beta" else "")
    url = f"https://github.com/{info['repository']}#{branch}"
    files["repository.yaml"] = yaml.safe_dump({"name": title, "url": url}).encode()
    files["README.md"] = (
        f"# {title}\n\nInstall the Modbus service from `{url}`.\n\n"
        f"App version: {info['version']}. Source release: [{info['source_tag']}](https://github.com/{info['repository']}/releases/tag/{info['source_tag']}).\n"
        f"\nStandalone container reference for this release:\n\n```text\n{info['image']}@{digest}\n```\n\n"
        "This branch is generated after publication; source changes belong on main.\n"
    ).encode()
    files["release.json"] = (
        json.dumps(metadata, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def verify_catalog_tree(repository: str, commit: str, files: dict[str, bytes]) -> None:
    """Bind every installed catalog file to its reviewed content, not just metadata."""
    tree = api(repository, f"git/trees/{commit}?recursive=1")
    if tree is None or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise ValueError("app catalog tree is incomplete")
    expected = {
        path: hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False).hexdigest()
        for path, content in files.items()
    }
    observed = {}
    for item in tree["tree"]:
        if not isinstance(item, dict):
            raise ValueError("invalid app catalog tree entry")
        if item.get("type") == "tree" and item.get("mode") == "040000":
            continue
        path = item.get("path")
        if (
            not isinstance(path, str) or path in observed
            or item.get("type") != "blob" or item.get("mode") != "100644"
        ):
            raise ValueError("app catalog file types or paths differ")
        observed[path] = item.get("sha")
    if observed != expected:
        raise ValueError("app catalog files differ from the original release")


def verify_existing_catalog(info: dict[str, str], digest: str, branch: str) -> None:
    """Verify an unchanged app using its original source, including its old docs."""
    reference = api(info["repository"], f"git/ref/heads/{branch}")
    if reference is None:
        raise ValueError("app catalog reference is missing")
    files = catalog_files(info, digest, branch=branch, source_ref=info["commit"])
    verify_catalog_tree(info["repository"], reference["object"]["sha"], files)


def check_forward(
    previous: dict[str, Any], info: dict[str, str], digest: str,
    *, branch: str | None = None,
) -> bool:
    """Refuse downgrades and any changed content under an existing version."""
    branch = branch or info["channel"]
    metadata = _release_metadata(info, digest, branch)
    # Catalogs predating stable promotion used one channel field for both roles.
    previous = {
        "catalog_channel": previous.get("channel"),
        "source_tag": f"v{previous['version']}",
        **previous,
    }
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
    if remote_commit(repository, info["source_tag"]) != info["commit"]:
        raise ValueError("release tag changed before catalog publication")
    actual = existing_verified_image(info)
    if actual != digest:
        raise ValueError("release image tag changed before catalog publication")
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
                if Version(previous["version"]) == Version(info["version"]):
                    verify_catalog_tree(repository, parent, files)
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
    verify_catalog_tree(repository, commit["sha"], files)


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
