"""Compare service build inputs independently of HA and release version labels."""

from __future__ import annotations

import ast
import hashlib
import json
import shlex
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

import yaml
from release_version import validate_version

ROOT = Path(__file__).resolve().parents[1]
DEVICE = "custom_components/mennekes_amtron_rtu/device"
SERVICE_VERSION_FILE = f"{DEVICE}/service_version.py"
COMPAT_FILE = "custom_components/mennekes_amtron_rtu/remote_compat.py"
APP_DOCS = {f"modbus_service/{name}" for name in ("README.md", "DOCS.md", "CHANGELOG.md")}
FIXED_INPUTS = {
    "Dockerfile", ".dockerignore", "pyproject.toml", "requirements-service.txt",
    "LICENSE", "NOTICE", "modbus_service/config.yaml",
    "custom_components/mennekes_amtron_rtu/brand/icon.png",
}


def literal_version(source: str, name: str) -> str:
    """Read one literal version without importing a package or executing code."""
    tree = ast.parse(source)
    writes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == name
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    if len(writes) != 1 or len(assignments) != 1:
        raise ValueError(f"{name} must be a single literal version")
    value = assignments[0].value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        raise ValueError(f"{name} must be a single literal version")
    return validate_version(value.value)


def read_service_version(root: Path | None = None) -> str:
    """Require package, Supervisor and runtime to describe the same service."""
    root = root or ROOT
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    config = yaml.safe_load((root / "modbus_service/config.yaml").read_text())
    runtime = literal_version((root / SERVICE_VERSION_FILE).read_text(), "SERVICE_VERSION")
    if project["version"] != runtime or config["version"] != runtime:
        raise ValueError("service package, app and runtime versions must agree")
    return runtime


def minimum_service_version(root: Path | None = None) -> str:
    return literal_version(((root or ROOT) / COMPAT_FILE).read_text(), "MIN_SERVICE_VERSION")


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise ValueError("cannot read the service source revision")
    return result.stdout


def _copy_sources(dockerfile: str) -> set[str]:
    """Discover literal local COPY inputs; unknown source syntax fails closed."""
    sources: set[str] = set()
    for line in dockerfile.replace("\\\r\n", " ").replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        instruction, *tail = line.split(None, 1)
        arguments = tail[0] if tail else ""
        if instruction.upper() == "RUN" and "--mount=" in arguments:
            raise ValueError("RUN mounts need an explicit service input policy")
        if instruction.upper() == "ADD":
            raise ValueError("ADD needs an explicit service input policy; use local COPY")
        if instruction.upper() != "COPY":
            continue
        # A stage-to-stage copy has no new local build-context input.
        while arguments.startswith("--"):
            option, *tail = arguments.split(None, 1)
            if not tail or "=" not in option:
                raise ValueError("unsupported COPY option in service build")
            arguments = tail[0]
            if option.startswith("--from="):
                arguments = ""
                break
            arguments = arguments.lstrip()
        if not arguments:
            continue
        parts = json.loads(arguments) if arguments.startswith("[") else shlex.split(arguments)
        if not isinstance(parts, list) or len(parts) < 2:
            raise ValueError("invalid service COPY inputs")
        for source in parts[:-1]:
            if (
                not isinstance(source, str) or not source or source in {".", "./"}
                or source.startswith("/") or ".." in PurePosixPath(source).parts
                or any(char in source for char in "$*?[")
            ):
                raise ValueError("service COPY sources must be explicit local paths")
            sources.add(PurePosixPath(source).as_posix().rstrip("/"))
    return sources


def _normalized(path: str, content: bytes) -> bytes:
    if path == "pyproject.toml":
        project = tomllib.loads(content.decode())
        value = {key: project[key] for key in ("build-system", "project")}
        value["project"].pop("version", None)
        value["setuptools"] = project.get("tool", {}).get("setuptools", {})
    elif path == "modbus_service/config.yaml":
        value = yaml.safe_load(content)
        value.pop("version", None)
    elif path == SERVICE_VERSION_FILE:
        literal_version(content.decode(), "SERVICE_VERSION")
        tree = ast.parse(content)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "SERVICE_VERSION"
                for target in node.targets
            ):
                node.value = ast.Constant(value="<service-version>")
        return ast.dump(tree, include_attributes=False).encode()
    else:
        return content
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint(ref: str, root: Path | None = None) -> str:
    """Hash the exact Git tree's app inputs, excluding docs and version labels."""
    root = root or ROOT
    commit = git(root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
    entries: dict[str, tuple[str, str, str]] = {}
    for record in git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if record:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode().split()
            entries[raw_path.decode()] = (mode, kind, object_id)

    def blob(path: str) -> bytes:
        if path not in entries or entries[path][0] not in {"100644", "100755"} or entries[path][1] != "blob":
            raise ValueError(f"service input is missing or not a regular file: {path}")
        return git(root, "cat-file", "blob", entries[path][2])

    selected = set(FIXED_INPUTS)
    selected.update(path for path in entries if path.startswith(f"{DEVICE}/"))
    selected.update(path for path in entries if path.startswith("modbus_service/") and path not in APP_DOCS)
    for source in _copy_sources(blob("Dockerfile").decode()):
        matched = {path for path in entries if path == source or path.startswith(source + "/")}
        if not matched:
            raise ValueError(f"service COPY input is absent from Git: {source}")
        selected.update(matched)
    digest = hashlib.sha256()
    for path in sorted(selected):
        content = _normalized(path, blob(path))
        # Include paths, modes and lengths so renames/deletions/executability
        # changes are meaningful and concatenation cannot alias another tree.
        digest.update(f"{path}\0{entries[path][0]}\0{len(content)}\0".encode())
        digest.update(content)
    return digest.hexdigest()
