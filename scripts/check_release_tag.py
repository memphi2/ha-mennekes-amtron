#!/usr/bin/env python3
"""Verify that a release tag matches the integration version."""

from __future__ import annotations

import argparse
import sys

from check_reporting import report_failures
from manifest_version import read_integration_version


def main() -> int:
    """Compare the release tag with the manifest version."""

    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--attestation-context", action="store_true")
    args = parser.parse_args()

    failures = check_tag(args.tag)
    return report_failures(failures, f"Release tag {args.tag} validated")


def check_tag(tag: str) -> list[str]:
    """Return every disagreement between a tag and the manifest version."""

    version = read_integration_version()
    if not tag.startswith("v"):
        return [f"release tag {tag!r} must start with 'v'"]
    if tag[1:] != version:
        return [
            f"release tag {tag!r} does not match manifest version {version!r}"
        ]
    return []


if __name__ == "__main__":
    sys.exit(main())
