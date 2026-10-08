"""The bundled blueprints and how they reach the user's configuration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from custom_components.mennekes_amtron.blueprint_installer import (
    MANIFEST_NAME,
    SOURCE_DIR,
    digest,
    install_blueprints,
)

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "solar_surplus_charging.yaml",
    "pause_and_resume_on_surplus.yaml",
    "recover_from_lost_heartbeat.yaml",
    "downgrade_notification.yaml",
}


def _shipped() -> list[Path]:
    return sorted(SOURCE_DIR.glob("*.yaml"))


def test_the_expected_blueprints_ship() -> None:
    assert {path.name for path in _shipped()} == EXPECTED


@pytest.mark.parametrize("path", _shipped(), ids=lambda path: path.name)
def test_every_blueprint_passes_the_home_assistant_schema(path: Path) -> None:
    from homeassistant.components.blueprint.models import Blueprint
    from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
    from homeassistant.util.yaml import load_yaml_dict

    blueprint = Blueprint(
        load_yaml_dict(str(path)),
        expected_domain="automation",
        path=str(path),
        schema=BLUEPRINT_SCHEMA,
    )
    assert blueprint.name.startswith("MENNEKES AMTRON")
    assert blueprint.inputs
    assert blueprint.metadata["description"].strip()
    # A blueprint that claims an older Home Assistant than the integration
    # supports would install into a version the integration refuses to run on.
    assert blueprint.metadata["homeassistant"]["min_version"] == _minimum_ha()


@pytest.mark.parametrize("path", _shipped(), ids=lambda path: path.name)
def test_every_blueprint_input_is_selectable(path: Path) -> None:
    """A blueprint input without a selector is a free-text box in the UI."""

    from homeassistant.util.yaml import load_yaml_dict

    inputs = load_yaml_dict(str(path))["blueprint"]["input"]
    for name, definition in inputs.items():
        assert "selector" in definition, f"{path.name}: {name} has no selector"
        assert definition.get("name"), f"{path.name}: {name} has no label"
        assert definition.get("description"), f"{path.name}: {name} is unexplained"


@pytest.mark.parametrize("path", _shipped(), ids=lambda path: path.name)
def test_entity_inputs_are_filtered_to_this_integration(path: Path) -> None:
    """Otherwise the picker offers every entity in the installation."""

    from homeassistant.util.yaml import load_yaml_dict

    inputs = load_yaml_dict(str(path))["blueprint"]["input"]
    for name, definition in inputs.items():
        entity = definition["selector"].get("entity")
        if entity is None:
            continue
        integrations = {
            entry.get("integration")
            for entry in entity["filter"]
            if isinstance(entry, dict)
        }
        # a PV surplus sensor comes from somewhere else, everything else is ours
        assert integrations <= {"mennekes_amtron", None}, f"{path.name}: {name}"


def test_installing_into_an_empty_configuration(tmp_path: Path) -> None:
    report = install_blueprints(SOURCE_DIR, tmp_path)

    assert set(report.installed) == EXPECTED
    assert report.updated == []
    assert report.kept == []
    assert report.changed
    assert {path.name for path in tmp_path.glob("*.yaml")} == EXPECTED
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest) == EXPECTED


def test_installing_twice_changes_nothing(tmp_path: Path) -> None:
    install_blueprints(SOURCE_DIR, tmp_path)
    report = install_blueprints(SOURCE_DIR, tmp_path)

    assert not report.changed
    assert report.installed == []
    assert report.updated == []


def test_an_edited_blueprint_is_left_alone(tmp_path: Path) -> None:
    """A file the user changed is theirs."""

    install_blueprints(SOURCE_DIR, tmp_path)
    edited = tmp_path / "downgrade_notification.yaml"
    edited.write_text("# mine now\n", encoding="utf-8")

    report = install_blueprints(SOURCE_DIR, tmp_path)

    assert report.kept == ["downgrade_notification.yaml"]
    assert edited.read_text(encoding="utf-8") == "# mine now\n"


def test_an_untouched_blueprint_is_updated(tmp_path: Path) -> None:
    install_blueprints(SOURCE_DIR, tmp_path)
    target = tmp_path / "downgrade_notification.yaml"
    shipped = (SOURCE_DIR / "downgrade_notification.yaml").read_text(encoding="utf-8")

    # pretend an older version was installed and never touched
    older = "# older version\n" + shipped
    target.write_text(older, encoding="utf-8")
    manifest = tmp_path / MANIFEST_NAME
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    recorded["downgrade_notification.yaml"] = digest(older)
    manifest.write_text(json.dumps(recorded), encoding="utf-8")

    report = install_blueprints(SOURCE_DIR, tmp_path)

    assert report.updated == ["downgrade_notification.yaml"]
    assert target.read_text(encoding="utf-8") == shipped


def test_a_retired_blueprint_is_removed_only_if_untouched(tmp_path: Path) -> None:
    install_blueprints(SOURCE_DIR, tmp_path)
    manifest = tmp_path / MANIFEST_NAME
    recorded = json.loads(manifest.read_text(encoding="utf-8"))

    gone = tmp_path / "retired.yaml"
    gone.write_text("retired\n", encoding="utf-8")
    recorded["retired.yaml"] = digest("retired\n")

    adopted = tmp_path / "customised.yaml"
    adopted.write_text("mine\n", encoding="utf-8")
    recorded["customised.yaml"] = digest("something else\n")
    manifest.write_text(json.dumps(recorded), encoding="utf-8")

    report = install_blueprints(SOURCE_DIR, tmp_path)

    assert report.removed == ["retired.yaml"]
    assert not gone.exists()
    assert adopted.exists()


def test_a_broken_manifest_is_survived(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / MANIFEST_NAME).write_text("not json", encoding="utf-8")

    report = install_blueprints(SOURCE_DIR, tmp_path)

    assert set(report.installed) == EXPECTED


def test_a_manifest_that_is_not_a_mapping_is_survived(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text("[1, 2]", encoding="utf-8")
    assert set(install_blueprints(SOURCE_DIR, tmp_path).installed) == EXPECTED


def test_setup_installs_them_through_the_executor() -> None:
    from custom_components.mennekes_amtron.blueprint_installer import (
        async_install_blueprints,
    )
    from tests.ha_fakes import FakeHass

    async def run() -> None:
        hass = FakeHass()
        target = Path(hass.config_dir) / "blueprints" / "automation" / "mennekes_amtron"
        report = await async_install_blueprints(hass)
        assert set(report.installed) == EXPECTED
        assert (target / "solar_surplus_charging.yaml").is_file()

    asyncio.run(run())


def _minimum_ha() -> str:
    """Return the oldest Home Assistant the project supports."""

    versions = json.loads(
        (ROOT / "project-versions.json").read_text(encoding="utf-8")
    )
    return str(versions["min_homeassistant"])
