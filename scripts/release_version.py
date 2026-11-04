"""One release-version policy for HA, the service image and catalog channels."""

from __future__ import annotations

import re

VERSION_PATTERN = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:(?:b|rc)[1-9]\d*)?"


def validate_version(value: str) -> str:
    """Allow canonical stable, beta and release-candidate versions only."""
    if not re.fullmatch(VERSION_PATTERN, value):
        raise ValueError("expected X.Y.Z, X.Y.ZbN or X.Y.ZrcN")
    return value


def is_prerelease(tag: str) -> bool:
    """Require a versioned tag before deciding its publication channel."""
    if not tag.startswith("v"):
        raise ValueError("release tag must start with v")
    return bool(re.search(r"(?:b|rc)\d+$", validate_version(tag[1:])))


def catalog_branch(version: str) -> str:
    """Beta builds must never update the stable app catalog."""
    return "apps-beta" if is_prerelease(f"v{version}") else "apps"
