"""Build, exercise and publish the service without advertising unavailable images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from manifest_version import read_integration_version
from release_check import remote_commit, run
from release_version import catalog_branch, validate_version
from service_inputs import read_service_version

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURES = {"amd64", "aarch64"}
OCI_ARCH = {"amd64": "amd64", "aarch64": "arm64"}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
APP_ASSETS = {
    "/": "app_settings.html",
    "/app_settings.js": "app_settings.js",
    "/app_settings.css": "app_settings.css",
}


def release_info(repository: str, tag: str) -> dict[str, str]:
    """Keep the app version separate from its reviewed integration source tag."""
    if repository != "memphi2/ha-mennekes-amtron":
        raise ValueError("service publication is restricted to its source repository")
    integration_version = validate_version(tag.removeprefix("v"))
    if tag != f"v{integration_version}" or read_integration_version() != integration_version:
        raise ValueError("release tag differs from the integration")
    version = read_service_version(ROOT)
    config = yaml.safe_load((ROOT / "modbus_service/config.yaml").read_text())
    image = f"ghcr.io/{repository}-service"
    if "image" in config or set(config["arch"]) != ARCHITECTURES:
        raise ValueError("app image or architecture contract differs")
    commit = run("git", "rev-parse", "HEAD").decode().strip()
    return {
        "image": image,
        "version": version,
        "commit": commit,
        "source_tag": tag,
        "channel": catalog_branch(version),
        "repository": repository,
    }


def check_installed_assets(container: str) -> None:
    """Exercise packaged UI handlers in an isolated process in the running image."""
    asset_directory = ROOT / "custom_components/mennekes_amtron_rtu/device"
    expected_assets = {
        path: hashlib.sha256((asset_directory / filename).read_bytes()).hexdigest()
        for path, filename in APP_ASSETS.items()
    }
    run(
        "docker", "exec", container, "python", "-c",
        (ROOT / "scripts/check_service_assets.py").read_text(),
        json.dumps(expected_assets),
    )


def check_installed_authentication(container: str) -> None:
    """Exercise the installed cipher and full login on loopback under policy."""
    run(
        "docker", "exec", container, "python", "-c",
        (ROOT / "scripts/check_service_auth.py").read_text(),
    )


def smoke(image: str, *, apparmor_profile: str | None = None) -> None:
    """Exercise the installed container API with no hardware or HA dependency."""
    if apparmor_profile is not None and (
        not isinstance(apparmor_profile, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", apparmor_profile)
        or apparmor_profile == "unconfined"
    ):
        raise ValueError("invalid AppArmor profile name")
    confinement = (
        ("--init", f"--security-opt=apparmor={apparmor_profile}")
        if apparmor_profile is not None else ()
    )
    with tempfile.TemporaryDirectory(prefix="amtron-container-") as temporary:
        directory = Path(temporary)
        token = secrets.token_urlsafe(32)
        options = directory / "options.json"
        options.write_text(
            json.dumps(
                {
                    "connection_type": "USB / RS-485",
                    "serial_port": "/dev/amtron-test-absent",
                    # Supervisor's list(...) selector serializes numbers as text.
                    "baudrate": "57600",
                    "token": token,
                    "read_only": True,
                    "listen": "0.0.0.0",  # noqa: S104 - container is published on loopback only
                }
            )
        )
        options.chmod(0o600)
        container = (
            run(
                "docker",
                "run",
                "--detach",
                *confinement,
                # Match the owner of the private bind mount without granting
                # DAC override to container root or weakening the token file.
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs",
                "/tmp",  # noqa: S108 - isolated container tmpfs
                "--publish",
                "127.0.0.1::8765",
                "--volume",
                f"{directory}:/data",
                image,
            )
            .decode()
            .strip()
        )
        try:
            port = json.loads(run("docker", "inspect", container))[0][
                "NetworkSettings"
            ]["Ports"]["8765/tcp"][0]["HostPort"]
            base = f"http://127.0.0.1:{int(port)}"
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(  # noqa: S310 - fixed loopback HTTP
                        base + "/health", timeout=2
                    ) as response:
                        if response.status == 200:
                            break
                except OSError, urllib.error.URLError:
                    if time.monotonic() >= deadline:
                        logs = subprocess.run(
                            ["docker", "logs", "--tail", "40", container],
                            capture_output=True,
                            check=False,
                        )
                        detail = (logs.stdout + logs.stderr).decode(errors="replace")
                        raise ValueError(
                            "container API did not become ready: "
                            + detail.replace(token, "<redacted>")
                        ) from None
                time.sleep(0.25)
            try:
                urllib.request.urlopen(base + "/v2/state", timeout=2)  # noqa: S310 - fixed loopback HTTP
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise ValueError("unexpected unauthenticated response") from error
            else:
                raise ValueError("container API allowed unauthenticated access")
            request = urllib.request.Request(  # noqa: S310 - fixed loopback HTTP
                base + "/v2/state", headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 - fixed loopback HTTP
                data = json.load(response)
            if (
                data.get("api_version") != 2
                or data.get("available") is not False
                or data["control"]["read_only"] is not True
                or data["heartbeat"]["running"] is not False
            ):
                raise ValueError("container did not report the missing device safely")
            # Read through the installed package's HTTP handlers, not through
            # the source checkout. This runs for both native architecture builds
            # under their actual AppArmor profile before any candidate is pushed.
            check_installed_assets(container)
            check_installed_authentication(container)
            run("docker", "stop", "--time", "15", container)
            if (
                json.loads(run("docker", "inspect", container))[0]["State"]["ExitCode"]
                != 0
            ):
                raise ValueError("container did not shut down cleanly")
            lifecycle_log = run("docker", "logs", container).decode(errors="replace")
            if token in lifecycle_log or any(
                message not in lifecycle_log
                for message in (
                    "Starting Mennekes Amtron Modbus service",
                    "Service API ready",
                    "Opening serial connection",
                    "SIGTERM received",
                    "Modbus service shutdown complete",
                )
            ):
                raise ValueError("container lifecycle logs are missing or expose credentials")
        finally:
            run("docker", "rm", "--force", container)


def build(info: dict[str, str], architecture: str, output: Path) -> None:
    """Push a uniquely named candidate only after native container smoke tests."""
    if architecture not in ARCHITECTURES:
        raise ValueError("unsupported build architecture")
    attempt = f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    if not re.fullmatch(r"\d+-\d+", attempt):
        raise ValueError("invalid workflow attempt")
    candidate = f"{info['image']}:build-{attempt}-{architecture}"
    command = ["docker", "build", "--tag", candidate]
    for key, value in {
        "source": f"https://github.com/{info['repository']}",
        "revision": info["commit"],
        "version": info["version"],
        "licenses": "Apache-2.0",
    }.items():
        command.extend(("--label", f"org.opencontainers.image.{key}={value}"))
    subprocess.run([*command, "."], cwd=ROOT, check=True)
    run("docker", "run", "--rm", candidate, "python", "-m", "pip", "check")
    run("docker", "run", "--rm", candidate, "amtron-modbus-service", "--help")
    # The mandatory harness also runs the API/auth/SIGTERM smoke under the
    # actual profile; an unavailable or rejected profile must stop publication.
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_service_apparmor.py"), "--image", candidate],
        cwd=ROOT,
        check=True,
    )
    if remote_commit(info["repository"], info["source_tag"]) != info["commit"]:
        raise ValueError("release tag changed before pushing image candidates")
    subprocess.run(["docker", "push", candidate], check=True)
    # Read the push result from the local daemon. A registry writer may replace
    # the staging tag, so resolving that mutable remote tag is not build evidence.
    pushed = json.loads(
        run(
            "docker", "image", "inspect", candidate, "--format", "{{json .RepoDigests}}"
        )
    )
    if not isinstance(pushed, list) or not all(
        isinstance(item, str) for item in pushed
    ):
        raise ValueError("invalid local image push result")
    digests = [
        item.removeprefix(f"{info['image']}@")
        for item in pushed
        if item.startswith(f"{info['image']}@")
    ]
    if len(digests) != 1:
        raise ValueError("local image push result is missing or ambiguous")
    digest = digests[0]
    if not DIGEST.fullmatch(digest):
        raise ValueError("invalid candidate image digest")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({**info, "architecture": architecture, "digest": digest}) + "\n"
    )


def validate_candidates(directory: Path, info: dict[str, str]) -> dict[str, str]:
    """Reject wrong-source, duplicate and incomplete workflow artifacts."""
    candidates: dict[str, str] = {}
    files = sorted(directory.glob("*.json"))
    if len(files) != 2:
        raise ValueError("expected exactly two native image results")
    for file in files:
        item = json.loads(file.read_text())
        arch, digest = item.get("architecture"), item.get("digest")
        if (
            any(item.get(key) != value for key, value in info.items())
            or arch not in ARCHITECTURES
            or arch in candidates
            or not isinstance(digest, str)
            or not DIGEST.fullmatch(digest)
        ):
            raise ValueError("candidate identity or architecture differs")
        candidates[arch] = digest
    return candidates


def inspect_index(reference: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", reference],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # Only an explicit missing manifest permits a new immutable version tag.
        error = result.stderr.decode().lower()
        if missing_ok and (
            "manifest unknown" in error or error.rstrip().endswith(": not found")
        ):
            return None
        raise ValueError("cannot inspect registry manifest")
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError("invalid registry manifest")
    return data


def verify_provenance(info: dict[str, str], digest: str) -> None:
    """Require an attested immutable image from its original reviewed source."""
    if not DIGEST.fullmatch(digest):
        raise ValueError("invalid published image digest")
    run(
        "gh", "attestation", "verify", f"oci://{info['image']}@{digest}",
        "--repo", info["repository"],
        "--signer-workflow", f"{info['repository']}/.github/workflows/release.yml",
        "--source-ref", f"refs/tags/{info['source_tag']}",
        "--source-digest", info["commit"],
    )


def existing_verified_image(info: dict[str, str]) -> str | None:
    """Reuse only a public, attested image; absence is not an access failure."""
    if remote_commit(info["repository"], info["source_tag"]) != info["commit"]:
        raise ValueError("release source tag changed before image verification")
    reference = f"{info['image']}:{info['version']}"
    if inspect_index(reference, missing_ok=True) is None:
        return None
    digest = run(
        "docker", "buildx", "imagetools", "inspect", reference,
        "--format", "{{.Manifest.Digest}}",
    ).decode().strip()
    verify_provenance(info, digest)
    verify_public(info, digest)
    return digest


def verify_index(
    index: dict[str, Any], info: dict[str, str], candidates: dict[str, str]
) -> None:
    """Match native build digests before trusting an index or its source labels."""
    manifests = index.get("manifests", [])
    if len(manifests) != 2:
        raise ValueError("expected two platform manifests")
    architectures = set()
    expected = {OCI_ARCH[arch]: digest for arch, digest in candidates.items()}
    for descriptor in manifests:
        arch = descriptor["platform"]["architecture"]
        digest = descriptor.get("digest", "")
        if (
            arch not in OCI_ARCH.values()
            or arch in architectures
            or descriptor["platform"].get("os") != "linux"
            or not DIGEST.fullmatch(digest)
            or digest != expected.get(arch)
        ):
            raise ValueError("image platform or digest differs from validated builds")
        architectures.add(arch)
        reference = f"{info['image']}@{digest}"
        run("docker", "pull", "--platform", f"linux/{arch}", reference)
        image = json.loads(run("docker", "image", "inspect", reference))[0]
        labels = image["Config"]["Labels"]
        if (
            image["Architecture"] != arch
            or image["Os"] != "linux"
            or labels.get("org.opencontainers.image.revision") != info["commit"]
            or labels.get("org.opencontainers.image.version") != info["version"]
            or labels.get("org.opencontainers.image.source")
            != f"https://github.com/{info['repository']}"
        ):
            raise ValueError("published image does not match the release source")


def publish(info: dict[str, str], directory: Path) -> str:
    """Never replace an existing release tag, including on a workflow rerun."""
    candidates = validate_candidates(directory, info)
    if remote_commit(info["repository"], info["source_tag"]) != info["commit"]:
        raise ValueError("release tag changed before publishing the image")
    reference = f"{info['image']}:{info['version']}"
    index = inspect_index(reference, missing_ok=True)
    if index is None:
        # Validate candidate source/platforms before making the public tag.
        descriptors = [
            {
                "digest": digest,
                "platform": {"os": "linux", "architecture": OCI_ARCH[arch]},
            }
            for arch, digest in sorted(candidates.items())
        ]
        verify_index({"manifests": descriptors}, info, candidates)
        run(
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            reference,
            *(f"{info['image']}@{digest}" for digest in candidates.values()),
        )
    digest = (
        run(
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{.Manifest.Digest}}",
        )
        .decode()
        .strip()
    )
    if not DIGEST.fullmatch(digest):
        raise ValueError("invalid published image digest")
    # Resolve the mutable tag once, then verify exactly the immutable digest
    # that the workflow will attest. Never attest merely matching OCI labels.
    index = inspect_index(f"{info['image']}@{digest}")
    if index is None:
        raise ValueError("published manifest was not confirmed")
    verify_index(index, info, candidates)
    return digest


def app_dockerfile(info: dict[str, str], digest: str) -> bytes:
    """Bind Supervisor installs to the attested image, never a mutable tag."""
    image = info["image"]
    if image != "ghcr.io/memphi2/ha-mennekes-amtron-service" or not DIGEST.fullmatch(
        digest
    ):
        raise ValueError("invalid app image or digest")
    # Supervisor appends :version to config.image, so digest references must
    # use its supported local-build path. No source is compiled on the host.
    return f"FROM {image}@{digest}\n".encode()


def verify_app_install(info: dict[str, str], digest: str) -> None:
    """Build and exercise exactly the Dockerfile consumed by Supervisor."""
    with tempfile.TemporaryDirectory(prefix="amtron-app-install-") as temporary:
        context = Path(temporary)
        (context / "Dockerfile").write_bytes(app_dockerfile(info, digest))
        run("docker", "build", "--pull", "--tag", "amtron-app:verify", temporary)
        smoke("amtron-app:verify")


def verify_public(info: dict[str, str], digest: str) -> None:
    """A catalog may reference only images that users can pull without a login."""
    if not DIGEST.fullmatch(digest):
        raise ValueError("invalid published image digest")
    with tempfile.TemporaryDirectory(prefix="amtron-anonymous-") as temporary:
        environment = dict(os.environ, DOCKER_CONFIG=temporary, DOCKER_AUTH_CONFIG="")
        result = subprocess.run(
            ["docker", "manifest", "inspect", f"{info['image']}@{digest}"],
            capture_output=True,
            check=False,
            env=environment,
        )
    if result.returncode:
        raise ValueError(
            "image is not publicly pullable; set the GitHub container package visibility to public before retrying"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("prepare", "build", "publish", "public", "app", "smoke", "existing")
    )
    parser.add_argument("--repository", default="memphi2/ha-mennekes-amtron")
    parser.add_argument("--tag")
    parser.add_argument("--architecture", choices=sorted(ARCHITECTURES))
    parser.add_argument("--path", type=Path)
    parser.add_argument("--digest")
    parser.add_argument("--image")
    args = parser.parse_args()
    try:
        if args.action == "smoke" and args.image:
            smoke(args.image)
            return 0
        info = release_info(args.repository, args.tag or "")
        if args.action == "build" and args.architecture and args.path:
            build(info, args.architecture, args.path)
        elif args.action == "publish" and args.path:
            info["digest"] = publish(info, args.path)
        elif args.action == "public" and args.digest:
            verify_public(info, args.digest)
        elif args.action == "app" and args.digest:
            verify_app_install(info, args.digest)
        elif args.action == "existing":
            digest = existing_verified_image(info)
            if digest is None:
                raise ValueError("expected published service image is missing")
            if args.digest and digest != args.digest:
                raise ValueError("published service image differs from the release plan")
            info["digest"] = digest
        elif args.action != "prepare":
            raise ValueError("missing action arguments")
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a") as stream:
                stream.write("".join(f"{key}={value}\n" for key, value in info.items()))
        sys.stdout.write(json.dumps(info) + "\n")
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.CalledProcessError,
    ) as error:
        sys.stderr.write(f"Service publication stopped: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
