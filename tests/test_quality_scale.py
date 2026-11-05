"""Quality labels must follow the rule inventory, evidence and documented counts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts import check_quality_scale as quality


def status_table(statuses):
    counts = {status: sum(value == status for value in statuses.values()) for status in quality.VALID_STATUSES}
    counts["total"] = len(statuses)
    return "| Status | Count |\n| --- | --- |\n" + "\n".join(
        f"| {name} | {count} |" for name, count in counts.items()
    )


def test_official_inventory_includes_all_current_tiers_and_new_documentation_rules():
    assert [len(rules) for rules in quality.TIER_RULES.values()] == [20, 10, 21, 3]
    assert len(quality.OFFICIAL_RULES) == len(set(quality.OFFICIAL_RULES)) == 54
    assert {"docs-conditions", "docs-triggers"} <= set(quality.TIER_RULES["bronze"])


def test_yaml_parser_accepts_scalar_quoted_inline_and_annotated_statuses():
    rules = quality.parse_rules("""rules:
  action-setup: "done" # inline comment
  docs-actions: {status: done, comment: 'Evidence: [guide](docs/user-guide.md)'}
  docs-conditions:
    status: exempt
    comment: >-
      No custom conditions.
      See [examples](docs/automations.md).
""")
    assert rules["action-setup"] == quality.Rule("done")
    assert rules["docs-actions"].status == "done"
    assert rules["docs-conditions"].comment == "No custom conditions. See [examples](docs/automations.md)."


@pytest.mark.parametrize("text", [
    "rules: {}\nrules: {}",
    "rules: {brands: done, brands: exempt}",
    "rules: {brands: {status: done, status: exempt}}",
])
def test_duplicate_yaml_keys_cannot_silently_replace_audit_results(text):
    with pytest.raises(ValueError, match="duplicate"):
        quality.parse_rules(text)


@pytest.mark.parametrize("text", [
    "[]", "rules: []", "rules: {}\nunknown: true", "rules: {1: done}",
    "rules: {brands: true}", "rules: {brands: {status: done, invented: true}}",
    "rules: {brands: {status: done, comment: []}}",
])
def test_wrong_yaml_shapes_fail_closed(text):
    with pytest.raises(ValueError, match=r"quality|rules"):
        quality.parse_rules(text)


def test_unknown_missing_and_invalid_rules_are_rejected():
    statuses = quality.parse_quality_scale("rules: {invented-rule: done}")
    failures = quality.validate_rules(statuses)
    assert any("unknown rule" in failure for failure in failures)
    assert any("missing official rule" in failure for failure in failures)
    statuses = dict.fromkeys(quality.OFFICIAL_RULES, "done")
    statuses["brands"] = "maybe"
    assert any("invalid status" in failure for failure in quality.validate_rules(statuses))


def test_tier_requirements_derive_blockers_instead_of_hard_coding_a_success_label():
    statuses = dict.fromkeys(quality.OFFICIAL_RULES, "done")
    statuses["diagnostics"] = "todo"
    assert quality.validate_tier(statuses, "silver") == []
    assert quality.validate_tier(statuses, "gold") == ["gold has unresolved rule(s): diagnostics"]
    assert quality.validate_tier(statuses, "platinum") == ["platinum has unresolved rule(s): diagnostics"]
    statuses["diagnostics"] = "exempt"
    assert quality.validate_tier(statuses, "platinum") == []
    assert quality.validate_tier(statuses, "invented")


def test_evidence_requires_an_explanation_and_existing_local_code_symbol(tmp_path):
    path = tmp_path / "client.py"
    path.write_text("class Client:\n    async def connect(self):\n        pass\n")
    good = quality.Rule("done", "Connection code: [client](client.py#Client.connect).")
    assert quality.validate_evidence({"config-flow": good}, tmp_path) == []
    missing = replace(good, comment="[client](client.py#Client.missing)")
    assert any("symbol does not exist" in item for item in quality.validate_evidence({"config-flow": missing}, tmp_path))
    for rule in (
        quality.Rule("exempt"),
        quality.Rule("done", "Trust the status label."),
        quality.Rule("done", "[gone](missing.py)"),
        quality.Rule("done", "[external](../outside.py)"),
    ):
        assert quality.validate_evidence({"config-flow": rule}, tmp_path)


def test_document_counts_follow_yaml_including_new_exemptions_and_open_rules():
    statuses = dict.fromkeys(quality.OFFICIAL_RULES, "done")
    statuses["docs-triggers"] = "exempt"
    statuses["diagnostics"] = "todo"
    docs = status_table(statuses) + "\n`docs-triggers` does not apply. `diagnostics` is unfinished."
    assert quality.validate_documentation(docs, statuses) == []
    assert quality.validate_documentation(docs.replace("| total | 54 |", "| total | 52 |"), statuses)
    assert quality.validate_documentation(docs.replace("`diagnostics`", "diagnostics"), statuses)
    assert quality.validate_documentation(docs + "\n| done | 52 |", statuses)
    assert quality.validate_documentation("Current status: Platinum. Open rules: none.", statuses)


def test_every_audited_rule_has_reviewable_evidence_in_the_checkout():
    rules = quality.parse_rules(quality.QUALITY_SCALE.read_text())
    assert quality.validate_rules({name: rule.status for name, rule in rules.items()}) == []
    assert quality.validate_evidence(rules) == []
