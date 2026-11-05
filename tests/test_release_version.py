"""Stable installations must not be offered beta builds or invalid image tags."""

import pytest

from scripts.release_version import (
    catalog_branch,
    catalog_branches,
    is_prerelease,
    validate_version,
)


@pytest.mark.parametrize("version", ["0.5.0", "1.12.30", "0.5.0b1", "0.5.0rc2"])
def test_release_channels(version):
    assert validate_version(version) == version
    beta = "b" in version or "rc" in version
    assert is_prerelease(f"v{version}") is beta
    assert catalog_branch(version) == ("apps-beta" if beta else "apps")
    assert catalog_branches(version) == (("apps-beta",) if beta else ("apps", "apps-beta"))


@pytest.mark.parametrize(
    "version",
    [
        "main",
        "01.2.3",
        "0.5.0b0",
        "0.5.0-dev",
        "0.5.0+local",
        "0.5.0\n",
        "../../main",
        "0.5.0;command",
    ],
)
def test_invalid_versions_never_reach_commands_or_catalog_refs(version):
    with pytest.raises(ValueError, match="expected"):
        catalog_branch(version)


def test_tag_prefix_is_required():
    with pytest.raises(ValueError, match="start with v"):
        is_prerelease("0.5.0")
