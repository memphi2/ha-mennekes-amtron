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
