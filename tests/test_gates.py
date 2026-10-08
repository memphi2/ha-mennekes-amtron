"""Meta tests: the validation gates themselves have to work.

A gate that silently passes is worse than no gate, so every gate is run here
and the ones with a pure rule are also shown a broken input.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

GATES = (
    "check_repo.py",
    "check_legal_audit.py",
    "check_quality_scale.py",
    "check_register_map.py",
    "check_pymodbus_pin.py",
)


@pytest.mark.parametrize("gate", GATES)
def test_every_gate_passes_on_this_repository(gate: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / gate)],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_the_validation_sequence_matches_the_documented_order() -> None:
    import check_validate

    names = [step.name for step in check_validate.steps()]
    assert names == [
        "Repository checks",
        "Legal/provenance audit",
        "Quality scale checks",
        "Register map checks",
        "pymodbus requirement check",
        "Ruff",
        "Python tests",
        "Python coverage ratchet",
        "Python typing ratchet",
    ]


def test_the_quality_scale_gate_rejects_an_unknown_rule() -> None:
    import check_quality_scale

    statuses = check_quality_scale.parse_quality_scale(
        "rules:\n  made-up-rule: done\n"
    )
    failures = check_quality_scale.validate_rules(statuses)
    assert any("unknown rule" in failure for failure in failures)
    assert any("missing official rule" in failure for failure in failures)


def test_the_quality_scale_gate_rejects_an_invalid_status() -> None:
    import check_quality_scale

    statuses = dict.fromkeys(check_quality_scale.OFFICIAL_RULES, "done")
    statuses["brands"] = "maybe"
    failures = check_quality_scale.validate_rules(statuses)
    assert any("invalid status" in failure for failure in failures)


def test_the_quality_scale_gate_rejects_any_open_rule() -> None:
    import check_quality_scale

    assert not check_quality_scale.EXPECTED_PLATINUM_BLOCKERS

    statuses = dict.fromkeys(check_quality_scale.OFFICIAL_RULES, "done")
    statuses["diagnostics"] = "todo"
    assert any(
        "undocumented TODO blocker" in failure
        for failure in check_quality_scale.validate_rules(statuses)
    )
    assert any(
        "blockers drifted" in failure
        for failure in check_quality_scale.validate_blockers(statuses, "")
    )


def test_the_quality_scale_gate_accepts_a_fully_answered_scale() -> None:
    import check_quality_scale

    statuses = dict.fromkeys(check_quality_scale.OFFICIAL_RULES, "done")
    assert check_quality_scale.validate_blockers(statuses, "") == []


def test_the_quality_scale_gate_requires_its_document_phrases() -> None:
    import check_quality_scale

    failures = check_quality_scale.validate_documentation("nothing here")
    assert len(failures) == 3
    assert any("Open rules: none" in failure for failure in failures)


def test_the_coverage_ratchet_stays_above_the_platinum_target() -> None:
    import check_coverage

    assert check_coverage.MINIMUM_COVERAGE >= 95


def test_the_requirement_gate_accepts_a_minimum_below_the_core_pin() -> None:
    import check_pymodbus_pin

    operator, minimum = check_pymodbus_pin.manifest_requirement()
    assert operator == ">="
    core = check_pymodbus_pin.core_version()
    assert core is not None
    assert check_pymodbus_pin._as_tuple(core) >= check_pymodbus_pin._as_tuple(minimum)
    assert check_pymodbus_pin.check_pin() == []


def test_the_requirement_gate_rejects_an_exact_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import check_pymodbus_pin

    monkeypatch.setattr(
        check_pymodbus_pin, "manifest_requirement", lambda: ("==", "3.13.1")
    )
    failures = check_pymodbus_pin.check_pin()
    assert any("not an exact pin" in failure or "'>='" in failure for failure in failures)


def test_the_requirement_gate_rejects_a_minimum_above_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import check_pymodbus_pin

    monkeypatch.setattr(
        check_pymodbus_pin, "manifest_requirement", lambda: (">=", "99.0.0")
    )
    failures = check_pymodbus_pin.check_pin()
    assert any("below the declared minimum" in failure for failure in failures)


def test_the_requirement_gate_needs_a_pymodbus_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import check_pymodbus_pin

    monkeypatch.setattr(check_pymodbus_pin, "manifest_requirement", lambda: None)
    assert check_pymodbus_pin.check_pin() == [
        "manifest.json does not declare a pymodbus requirement"
    ]


def test_the_release_tag_gate_compares_against_the_manifest() -> None:
    import check_release_tag
    from manifest_version import read_integration_version

    version = read_integration_version()
    assert check_release_tag.check_tag(f"v{version}") == []
    assert check_release_tag.check_tag(version) != []
    assert check_release_tag.check_tag("v9.9.9") != []


def test_the_reporting_helper_separates_success_from_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from check_reporting import report_failures

    assert report_failures([], "fine") == 0
    assert capsys.readouterr().out == "fine\n"
    assert report_failures(["broken"]) == 1
    assert "FAIL: broken" in capsys.readouterr().err


def test_the_register_gate_notices_a_missing_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import check_register_map

    payload = json.loads(
        (check_register_map.COMPONENT / "strings.json").read_text(encoding="utf-8")
    )
    payload["entity"]["sensor"].pop("evse_state")
    monkeypatch.setattr(
        check_register_map, "_translation_payloads", lambda: {"en": payload}
    )
    failures = check_register_map.check_entity_translations()
    assert any("evse_state has no translation" in failure for failure in failures)


def test_the_register_gate_notices_a_missing_enum_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import check_register_map

    payload = json.loads(
        (check_register_map.COMPONENT / "strings.json").read_text(encoding="utf-8")
    )
    payload["entity"]["sensor"]["evse_state"]["state"].pop("charging")
    payload["entity"]["sensor"]["evse_state"]["state"]["made_up"] = "x"
    monkeypatch.setattr(
        check_register_map, "_translation_payloads", lambda: {"en": payload}
    )
    failures = check_register_map.check_enum_states()
    assert any("misses state(s) charging" in failure for failure in failures)
    assert any("unknown state(s) made_up" in failure for failure in failures)


def test_the_typing_gate_covers_every_module() -> None:
    import check_typing

    targets = set(check_typing.strict_targets())
    assert "custom_components/mennekes_amtron/control.py" in targets
    assert "custom_components/mennekes_amtron/registers.py" in targets
    assert "scripts/check_repo.py" in targets


def test_the_repository_gate_requires_usable_issue_forms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import check_repo

    assert check_repo.check_issue_templates() == []

    directory = tmp_path / ".github" / "ISSUE_TEMPLATE"
    directory.mkdir(parents=True)
    (directory / "config.yml").write_text("blank_issues_enabled: true\n", encoding="utf-8")
    for name in check_repo.ISSUE_FORMS:
        (directory / name).write_text("name: x\nbody: []\n", encoding="utf-8")
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)

    failures = check_repo.check_issue_templates()
    assert any("must disable blank issues" in failure for failure in failures)
    assert any("must ask for control_mode" in failure for failure in failures)
    assert any("must ask for registers" in failure for failure in failures)


def test_the_repository_gate_rejects_a_non_form_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import check_repo

    directory = tmp_path / ".github" / "ISSUE_TEMPLATE"
    directory.mkdir(parents=True)
    (directory / "config.yml").write_text(
        "blank_issues_enabled: false\n", encoding="utf-8"
    )
    for name in check_repo.ISSUE_FORMS:
        (directory / name).write_text("free text\n", encoding="utf-8")
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)

    failures = check_repo.check_issue_templates()
    assert len(failures) == len(check_repo.ISSUE_FORMS)
    assert all("is not an issue form" in failure for failure in failures)


def test_the_protected_checks_match_the_ci_jobs() -> None:
    import check_repo
    from apply_repo_settings import REQUIRED_CHECKS, steps

    assert check_repo.check_branch_protection_contexts() == []
    assert set(REQUIRED_CHECKS) == {
        "dependabot",
        "hacs",
        "hassfest",
        "validate (min-ha)",
        "validate (current-ha)",
    }
    names = [step.name for step in steps("owner/repo")]
    assert names[0] == "Repository settings"
    assert any(step.needs_public_or_pro for step in steps("owner/repo"))


def test_a_renamed_ci_job_breaks_branch_protection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import check_repo

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "validate.yml").write_text(
        "jobs:\n"
        "  hacs:\n    runs-on: ubuntu-24.04\n"
        "  hassfest:\n    runs-on: ubuntu-24.04\n"
        "  validate:\n    runs-on: ubuntu-24.04\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)

    failures = check_repo.check_branch_protection_contexts()
    assert any("no job 'dependabot'" in failure for failure in failures)
    assert any("no matrix entry 'min-ha'" in failure for failure in failures)


def test_the_settings_script_sends_nothing_without_apply(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import apply_repo_settings

    exit_code = apply_repo_settings.main([])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "would apply: Branch protection for main" in output
    assert "Nothing was sent" in output


def test_the_legal_gate_rejects_a_copy_of_the_vendor_specification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A text extraction of the manufacturer's PDF is still the PDF."""

    import check_legal_audit

    extraction = (
        "AMTRON 4You 300\n"
        "Modbus RTU Specification\n"
        "Doc. Revision:  2.5\n"
        "Modbus-Version:  v 01.03\n"
        "Release information\n"
        "The following functional codes can be used\n"
    )
    (tmp_path / "notes.txt").write_text(extraction, encoding="utf-8")
    (tmp_path / "citation.md").write_text(
        'See MENNEKES, "Modbus RTU Specification", revision 2.5.\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(check_legal_audit, "ROOT", tmp_path)

    failures = check_legal_audit._check_vendor_documents(
        [Path("notes.txt"), Path("citation.md")]
    )
    assert len(failures) == 1
    assert "notes.txt" in failures[0]


def test_the_legal_gate_allows_a_citation_of_the_specification() -> None:
    import check_legal_audit

    assert check_legal_audit._check_vendor_documents(
        [Path("docs/modbus-registers.md"), Path("docs/legal.md")]
    ) == []


def test_the_repository_gate_keeps_the_integration_marked_unofficial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The manifest name is what Home Assistant shows in the install list."""

    import check_repo

    assert check_repo.check_hacs_metadata() == []

    component = tmp_path / "custom_components" / "mennekes_amtron"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text(
        json.dumps({"name": "MENNEKES AMTRON"}), encoding="utf-8"
    )
    (tmp_path / "hacs.json").write_text(
        json.dumps(
            {
                "name": "MENNEKES AMTRON (Unofficial)",
                "homeassistant": check_repo.VERSION_CONFIG["min_homeassistant"],
                "filename": "ha-mennekes-amtron.zip",
                "zip_release": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)
    monkeypatch.setattr(check_repo, "COMPONENT", component)

    failures = check_repo.check_hacs_metadata()
    assert any("disagree" in failure for failure in failures)
    assert any("unofficial" in failure for failure in failures)


def test_the_register_gate_rejects_an_icon_that_hides_a_device_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device class carries its own, often state-dependent, icon."""

    import check_register_map

    assert check_register_map.check_entity_icons() == []

    icons = json.loads(
        (check_register_map.COMPONENT / "icons.json").read_text(encoding="utf-8")
    )
    icons["entity"]["sensor"]["temperature"] = {"default": "mdi:thermometer"}
    icons["entity"]["sensor"].pop("evse_state")
    path = check_register_map.COMPONENT / "icons.json"
    original = path.read_text(encoding="utf-8")
    try:
        path.write_text(json.dumps(icons), encoding="utf-8")
        failures = check_register_map.check_entity_icons()
    finally:
        path.write_text(original, encoding="utf-8")

    assert any("temperature declares an icon" in failure for failure in failures)
    assert any("evse_state has no icon" in failure for failure in failures)


def test_numeric_sensors_declare_a_display_precision() -> None:
    """Otherwise a float32 register shows as 230.10000610351562."""

    from custom_components.mennekes_amtron.sensor_descriptions import (
        SENSOR_DESCRIPTIONS,
    )

    missing = [
        description.key
        for description in SENSOR_DESCRIPTIONS
        if description.native_unit_of_measurement
        and description.suggested_display_precision is None
    ]
    assert missing == []


def test_the_hardware_document_keeps_its_sources_marked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It mixes the Modbus specification with the installation manual."""

    import check_repo

    assert check_repo.check_hardware_document() == []

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hardware.md").write_text("Just connect the wires.\n", encoding="utf-8")
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)

    failures = check_repo.check_hardware_document()
    assert any("(Spec)" in failure for failure in failures)
    assert any("(Manual)" in failure for failure in failures)
    assert any("electrician" in failure for failure in failures)
    assert any("XG1" in failure for failure in failures)
    assert any("exactly one master" in failure for failure in failures)


def test_the_schema_gate_rejects_a_voluptuous_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Home Assistant aliases the old name, so only a gate catches a relapse."""

    import check_repo

    assert check_repo.check_schema_library() == []

    (tmp_path / "flow.py").write_text(
        "import voluptuous as vol\n\nSCHEMA = vol.Schema({})\n", encoding="utf-8"
    )
    monkeypatch.setattr(check_repo, "ROOT", tmp_path)

    failures = check_repo.check_schema_library()
    assert len(failures) == 1
    assert "flow.py:1 imports voluptuous" in failures[0]


def test_the_schema_gate_allows_naming_voluptuous_in_prose() -> None:
    """The comments explaining the migration must not trip the gate."""

    import check_repo

    assert check_repo.VOLUPTUOUS_IMPORT.match("# voluptuous was replaced") is None
    assert check_repo.VOLUPTUOUS_IMPORT.match("import voluptuous_serialize") is None
    assert check_repo.VOLUPTUOUS_IMPORT.match("import voluptuous as vol") is not None
    assert check_repo.VOLUPTUOUS_IMPORT.match("from voluptuous import Schema")


def test_skipping_the_typing_gate_leaves_every_other_step() -> None:
    """The minimum matrix entry runs everything the annotations allow."""

    import check_validate

    full = [step.name for step in check_validate.steps()]
    reduced = [step.name for step in check_validate.steps(skip_typing=True)]

    assert check_validate.TYPING_STEP in full
    assert reduced == [name for name in full if name != check_validate.TYPING_STEP]
    assert len(reduced) == len(full) - 1
