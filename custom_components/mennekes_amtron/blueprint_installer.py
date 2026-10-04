"""Install the bundled automation blueprints into the user's configuration.

Home Assistant copies blueprints into ``config/blueprints/automation/`` only
for its own domain, so an integration that ships blueprints has to put them
there itself.

The rule that matters is not overwriting work: a file the user edited is
theirs. The installer records a digest of every file it writes, and on the
next start it only replaces a file that still matches what it wrote. Anything
else is left alone, including a file somebody created by hand under the same
name.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SOURCE_DIR = Path(__file__).with_name("blueprints") / "automation" / DOMAIN
TARGET_PARTS = ("blueprints", "automation", DOMAIN)
MANIFEST_NAME = f".{DOMAIN}_blueprints.json"


@dataclass(slots=True)
class InstallReport:
    """What one installation run did."""

    installed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Return true when anything on disk was written or deleted."""

        return bool(self.installed or self.updated or self.removed)


def digest(text: str) -> str:
    """Return the digest the manifest records for a blueprint's content."""

    return sha256(text.encode("utf-8")).hexdigest()


def install_blueprints(source: Path, target: Path) -> InstallReport:
    """Install, update and retire the bundled blueprints under ``target``.

    This is synchronous file work and is called from the executor.
    """

    report = InstallReport()
    if not source.is_dir():  # pragma: no cover - the package always ships them
        return report

    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / MANIFEST_NAME
    manifest = _read_manifest(manifest_path)
    shipped: dict[str, str] = {}

    for blueprint in sorted(source.glob("*.yaml")):
        content = blueprint.read_text(encoding="utf-8")
        shipped[blueprint.name] = digest(content)
        installed = target / blueprint.name
        if not installed.exists():
            installed.write_text(content, encoding="utf-8")
            report.installed.append(blueprint.name)
            continue
        current = digest(installed.read_text(encoding="utf-8"))
        if current == shipped[blueprint.name]:
            continue
        if manifest.get(blueprint.name) == current:
            installed.write_text(content, encoding="utf-8")
            report.updated.append(blueprint.name)
        else:
            report.kept.append(blueprint.name)

    for name, recorded in manifest.items():
        if name in shipped:
            continue
        retired = target / name
        if retired.is_file() and digest(retired.read_text(encoding="utf-8")) == recorded:
            retired.unlink()
            report.removed.append(name)

    manifest_path.write_text(
        json.dumps(shipped, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


async def async_install_blueprints(hass: HomeAssistant) -> InstallReport:
    """Install the bundled blueprints, off the event loop."""

    target = Path(hass.config.path(*TARGET_PARTS))
    report = await hass.async_add_executor_job(
        install_blueprints, SOURCE_DIR, target
    )
    if report.changed:
        _LOGGER.info(
            "Blueprints: installed %s, updated %s, removed %s",
            len(report.installed),
            len(report.updated),
            len(report.removed),
        )
    for name in report.kept:
        _LOGGER.debug("Blueprint %s was edited locally and is left alone", name)
    return report


def _read_manifest(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}
