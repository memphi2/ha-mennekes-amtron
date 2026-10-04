#!/usr/bin/env python3
"""Write the release checksums, build metadata and SPDX SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from manifest_version import read_integration_version

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Write every release asset next to the zip."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha256sums", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    args = parser.parse_args()

    version = read_integration_version()
    commit = _git_commit()
    zip_digest = _sha256(args.zip)

    args.metadata.write_text(
        json.dumps(
            {
                "release_tag": args.tag,
                "repository": args.repository,
                "integration_version": version,
                "git_commit": commit,
                "zip_sha256": zip_digest,
                "zip_entries": _zip_entries(args.zip),
                "built_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    args.sbom.write_text(
        json.dumps(_sbom(args, version, zip_digest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_sha256sums(args)
    sys.stdout.write(f"Wrote release assets for {args.tag}\n")
    return 0


def _write_sha256sums(args: argparse.Namespace) -> None:
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in (args.zip, args.metadata, args.sbom)
    ]
    args.sha256sums.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sbom(
    args: argparse.Namespace, version: str, zip_digest: str
) -> dict[str, object]:
    manifest = json.loads(
        (ROOT / "custom_components" / "mennekes_amtron" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    packages: list[dict[str, object]] = [
        {
            "SPDXID": "SPDXRef-Package-Integration",
            "name": "ha-mennekes-amtron",
            "versionInfo": version,
            "downloadLocation": f"https://github.com/{args.repository}",
            "filesAnalyzed": False,
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": zip_digest}],
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-Package-Integration",
        }
    ]
    for index, requirement in enumerate(manifest.get("requirements", [])):
        name, requirement_version = _split_requirement(str(requirement))
        spdx_id = f"SPDXRef-Package-Requirement-{index}"
        packages.append(
            {
                "SPDXID": spdx_id,
                "name": name,
                "versionInfo": requirement_version or "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-Integration",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": spdx_id,
            }
        )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"ha-mennekes-amtron-{version}",
        "documentNamespace": (
            f"https://github.com/{args.repository}/spdx/{uuid.uuid4()}"
        ),
        "creationInfo": {
            "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "creators": ["Tool: scripts/write_release_assets.py"],
        },
        "packages": packages,
        "relationships": relationships,
    }


def _split_requirement(requirement: str) -> tuple[str, str]:
    """Split a requirement into its name and its version constraint."""

    match = re.split(r"(==|>=|~=|>|<)", requirement, maxsplit=1)
    if len(match) == 3:
        return match[0], f"{match[1]}{match[2]}"
    return requirement, "NOASSERTION"


def _zip_entries(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return len(archive.namelist())


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() or "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
