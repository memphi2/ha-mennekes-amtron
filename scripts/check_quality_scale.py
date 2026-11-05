#!/usr/bin/env python3
"""Validate the local quality assessment and its reviewable evidence.

This checks structure, references, tier requirements and documentation drift.
Behavioral tests, coverage, typing and human review establish the actual quality;
a status label in this file cannot certify an integration on their behalf.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from check_reporting import report_failures
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "mennekes_amtron_rtu"
QUALITY_SCALE = COMPONENT / "quality_scale.yaml"
QUALITY_DOC = ROOT / "docs" / "quality-scale.md"

# Audited against https://contributors.home-assistant.io/core/quality-scale/rules/
# on 2026-11-05. Review this versioned inventory when HA adds or changes rules.
TIER_RULES = {
    "bronze": (
        "action-setup", "appropriate-polling", "brands", "common-modules",
        "config-flow", "config-flow-test-coverage", "dependency-transparency",
        "docs-actions", "docs-conditions", "docs-high-level-description",
        "docs-installation-instructions", "docs-removal-instructions",
        "docs-triggers", "entity-event-setup", "entity-unique-id", "has-entity-name",
        "runtime-data", "test-before-configure", "test-before-setup", "unique-config-entry",
    ),
    "silver": (
        "action-exceptions", "config-entry-unloading", "docs-configuration-parameters",
        "docs-installation-parameters", "entity-unavailable", "integration-owner",
        "log-when-unavailable", "parallel-updates", "reauthentication-flow", "test-coverage",
    ),
    "gold": (
        "devices", "diagnostics", "discovery", "discovery-update-info",
        "docs-data-update", "docs-examples", "docs-known-limitations",
        "docs-supported-devices", "docs-supported-functions", "docs-troubleshooting",
        "docs-use-cases", "dynamic-devices", "entity-category", "entity-device-class",
        "entity-disabled-by-default", "entity-translations", "exception-translations",
        "icon-translations", "reconfiguration-flow", "repair-issues", "stale-devices",
    ),
    "platinum": ("async-dependency", "inject-websession", "strict-typing"),
}
OFFICIAL_RULES = tuple(rule for rules in TIER_RULES.values() for rule in rules)
VALID_STATUSES = {"done", "todo", "exempt"}
EVIDENCE_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


@dataclass(frozen=True, slots=True)
class Rule:
    status: str
    comment: str = ""


def _check_unique_keys(node: Node, visited: set[int]) -> None:
    """Reject YAML duplicate keys instead of silently replacing audit entries."""
    if id(node) in visited:
        return
    visited.add(id(node))
    if isinstance(node, MappingNode):
        keys: set[str] = set()
        for key, value in node.value:
            if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                raise ValueError("quality-scale mapping keys must be strings")
            if key.value in keys:
                raise ValueError(f"duplicate quality-scale key: {key.value}")
            keys.add(key.value)
            _check_unique_keys(value, visited)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            _check_unique_keys(child, visited)


def parse_rules(text: str) -> dict[str, Rule]:
    """Read the official YAML shape, supporting scalar and annotated statuses."""
    node = yaml.compose(text)
    if node is not None:
        _check_unique_keys(node, set())
    document = yaml.safe_load(text)
    if not isinstance(document, dict) or set(document) != {"rules"}:
        raise ValueError("quality scale must contain exactly one rules mapping")
    raw_rules = document["rules"]
    if not isinstance(raw_rules, dict):
        raise ValueError("quality-scale rules must be a mapping")
    rules: dict[str, Rule] = {}
    for name, value in raw_rules.items():
        if isinstance(value, str):
            rules[name] = Rule(value)
            continue
        if not isinstance(value, dict) or set(value) - {"status", "comment"}:
            raise ValueError(f"invalid quality-scale rule fields: {name}")
        status, comment = value.get("status"), value.get("comment", "")
        if not isinstance(status, str) or not isinstance(comment, str):
            raise ValueError(f"quality-scale status and comment must be strings: {name}")
        rules[name] = Rule(status, comment)
    return rules


def parse_quality_scale(text: str) -> dict[str, str]:
    """Return statuses for callers that do not need evidence comments."""
    return {name: rule.status for name, rule in parse_rules(text).items()}


def validate_rules(statuses: dict[str, str]) -> list[str]:
    """Reject missing, unknown and invalid rule entries."""
    failures: list[str] = []
    expected, actual = set(OFFICIAL_RULES), set(statuses)
    for kind, names in (("missing official", expected - actual), ("unknown", actual - expected)):
        if names:
            failures.append(f"quality_scale.yaml has {kind} rule(s): {', '.join(sorted(names))}")
    invalid = sorted(f"{name}={status}" for name, status in statuses.items() if status not in VALID_STATUSES)
    if invalid:
        failures.append("quality_scale.yaml has invalid status value(s): " + ", ".join(invalid))
    return failures


def validate_tier(statuses: dict[str, str], tier: str) -> list[str]:
    """Derive blockers from the claimed manifest tier, including lower tiers."""
    if tier not in TIER_RULES:
        return [f"manifest.json has an unsupported quality tier: {tier}"]
    required: set[str] = set()
    for name, rules in TIER_RULES.items():
        required.update(rules)
        if name == tier:
            break
    blockers = sorted(name for name in required if statuses.get(name) not in {"done", "exempt"})
    return [f"{tier} has unresolved rule(s): {', '.join(blockers)}"] if blockers else []


def _python_symbols(path: Path) -> set[str]:
    """Resolve references to definitions without importing integration code."""
    symbols: set[str] = set()

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                symbols.add(name)
                visit(node.body, name + ".")

    visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body)
    return symbols


def validate_evidence(rules: dict[str, Rule], root: Path = ROOT) -> list[str]:
    """Require review notes and verify their local file/symbol references."""
    failures: list[str] = []
    for name, rule in rules.items():
        if not rule.comment.strip():
            failures.append(f"{name} needs a reason and evidence comment")
            continue
        references = [link for link in EVIDENCE_LINK.findall(rule.comment) if "://" not in link]
        if not references:
            failures.append(f"{name} needs at least one local evidence link")
        for reference in references:
            filename, _, symbol = reference.partition("#")
            path = (root / filename).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                failures.append(f"{name} has missing or external evidence: {reference}")
            elif symbol and path.suffix == ".py" and symbol not in _python_symbols(path):
                failures.append(f"{name} evidence symbol does not exist: {reference}")
    return failures


def validate_documentation(docs: str, statuses: dict[str, str]) -> list[str]:
    """Compare the document's status table and exemptions to the actual YAML."""
    expected = {status: sum(value == status for value in statuses.values()) for status in VALID_STATUSES}
    expected["total"] = len(statuses)
    counts: dict[str, int] = {}
    failures: list[str] = []
    for line in docs.splitlines():
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] in expected:
            name, value = cells
            if name in counts or not value.isdecimal():
                failures.append(f"docs/quality-scale.md has duplicate or invalid count: {name}")
            else:
                counts[name] = int(value)
    for status, count in expected.items():
        if counts.get(status) != count:
            failures.append(f"docs/quality-scale.md count for {status} must be {count}")
    for name, status in statuses.items():
        if status in {"todo", "exempt"} and f"`{name}`" not in docs:
            failures.append(f"docs/quality-scale.md must explain {status} rule `{name}`")
    return failures


def validate_quality_scale() -> list[str]:
    """Validate claims without substituting a canned success phrase for evidence."""
    try:
        rules = parse_rules(QUALITY_SCALE.read_text(encoding="utf-8"))
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        docs = QUALITY_DOC.read_text(encoding="utf-8")
    except (ValueError, OSError, yaml.YAMLError) as error:
        return [f"Unable to read quality assessment: {error}"]
    statuses = {name: rule.status for name, rule in rules.items()}
    return (
        validate_rules(statuses)
        + validate_tier(statuses, manifest.get("quality_scale", ""))
        + validate_evidence(rules)
        + validate_documentation(docs, statuses)
    )


def main() -> int:
    return report_failures(validate_quality_scale(), "Quality scale validation passed")


if __name__ == "__main__":
    sys.exit(main())
