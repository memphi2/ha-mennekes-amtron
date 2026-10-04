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
        "pymodbus pin check",
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


def test_the_quality_scale_gate_rejects_undocumented_blockers() -> None:
    import check_quality_scale

    statuses = dict.fromkeys(check_quality_scale.OFFICIAL_RULES, "done")
    statuses["diagnostics"] = "todo"
    failures = check_quality_scale.validate_rules(statuses)
    assert any("undocumented TODO blocker" in failure for failure in failures)


def test_the_quality_scale_gate_notices_blocker_drift() -> None:
    import check_quality_scale

    statuses = dict.fromkeys(check_quality_scale.OFFICIAL_RULES, "done")
    failures = check_quality_scale.validate_blockers(statuses, "")
    assert any("blockers drifted" in failure for failure in failures)
    assert any("`brands`" in failure for failure in failures)


def test_the_quality_scale_gate_requires_its_document_phrases() -> None:
    import check_quality_scale

    failures = check_quality_scale.validate_documentation("nothing here")
    assert len(failures) == 3


def test_the_pin_gate_reads_both_pins() -> None:
    import check_pymodbus_pin

    manifest_pin = check_pymodbus_pin.manifest_version()
    assert manifest_pin == "3.13.1"
    assert check_pymodbus_pin.core_version() == manifest_pin
    assert check_pymodbus_pin.check_pin() == []


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
