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
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

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
    failed: list[str] = field(default_factory=list)

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

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        report.failed.append("blueprint directory")
        return report
    manifest_path = target / MANIFEST_NAME
    manifest = _read_manifest(manifest_path)
    shipped = {path.name: path for path in sorted(source.glob("*.yaml"))}
    recorded = dict(manifest)

    for name, blueprint in shipped.items():
        try:
            content = blueprint.read_text(encoding="utf-8")
            installed = target / name
            if not installed.exists():
                _atomic_write(installed, content)
                report.installed.append(name)
            else:
                current = digest(installed.read_text(encoding="utf-8"))
                if current != digest(content):
                    if manifest.get(name) == current:
                        _atomic_write(installed, content)
                        report.updated.append(name)
                    else:
                        report.kept.append(name)
            recorded[name] = digest(content)
        except OSError, UnicodeError:
            report.failed.append(name)

    for name, checksum in manifest.items():
        if name in shipped:
            continue
        retired = target / name
        try:
            if (
                retired.is_file()
                and digest(retired.read_text(encoding="utf-8")) == checksum
            ):
                retired.unlink()
                report.removed.append(name)
            recorded.pop(name)
        except OSError, UnicodeError:
            report.failed.append(name)

    try:
        _atomic_write(
            manifest_path, json.dumps(recorded, indent=2, sort_keys=True) + "\n"
        )
    except OSError:
        report.failed.append(MANIFEST_NAME)
    return report


def _atomic_write(path: Path, content: str) -> None:
    """Keep the original intact if an update cannot be fully written."""

    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        temporary = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.close()
            temporary.replace(path)
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


async def async_install_blueprints(hass: HomeAssistant) -> InstallReport:
    """Install the bundled blueprints, off the event loop."""

    target = Path(hass.config.path(*TARGET_PARTS))
    report = await hass.async_add_executor_job(install_blueprints, SOURCE_DIR, target)
    if report.changed:
        _LOGGER.info(
            "Blueprints: installed %s, updated %s, removed %s",
            len(report.installed),
            len(report.updated),
            len(report.removed),
        )
    for name in report.kept:
        _LOGGER.debug("Blueprint %s was edited locally and is left alone", name)
    for name in report.failed:
        _LOGGER.warning(
            "Could not update %s; check blueprint files and permissions", name
        )
    return report


def _read_manifest(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str)
        and Path(key).name == key
        and key.endswith(".yaml")
        and isinstance(value, str)
    }
