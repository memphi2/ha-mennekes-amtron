"""Reject known obsolete owned APIs and blanket deprecation suppression."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBSOLETE = {"asyncio.iscoroutinefunction", "asyncio.coroutine", "datetime.utcnow", "datetime.datetime.utcnow"}
HA_METHODS = {"async_forward_entry_setup", "async_forward_entry_unload"}


def dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def suppressed_filter(value: str) -> bool:
    fields = value.split(":")
    return fields[0] == "ignore" and (len(fields) < 3 or fields[2] in {"", "Warning", "DeprecationWarning", "PendingDeprecationWarning", "FutureWarning"})


def source_issues(source: str) -> list[str]:
    """Inspect calls rather than matching historical prose or test strings."""
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update({a.asname or a.name: a.name for a in node.names})
        elif isinstance(node, ast.ImportFrom) and node.module:
            aliases.update({a.asname or a.name: f"{node.module}.{a.name}" for a in node.names})
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        root, _, tail = name.partition(".")
        name = aliases.get(root, root) + (f".{tail}" if tail else "")
        if name in OBSOLETE or name.rsplit(".", 1)[-1] in HA_METHODS:
            issues.append(f"line {node.lineno}: obsolete API {name}")
        if name in {"warnings.filterwarnings", "warnings.simplefilter"}:
            arguments = {k.arg: k.value for k in node.keywords}
            action = node.args[0] if node.args else arguments.get("action")
            category_index = 2 if name.endswith("filterwarnings") else 1
            category = node.args[category_index] if len(node.args) > category_index else arguments.get("category")
            if isinstance(action, ast.Constant) and action.value == "ignore" and (
                category is None or dotted(category) in {"Warning", "DeprecationWarning", "PendingDeprecationWarning", "FutureWarning"}
            ):
                issues.append(f"line {node.lineno}: deprecation warning suppression")
        if name == "pytest.mark.filterwarnings" and any(
            isinstance(a, ast.Constant) and isinstance(a.value, str) and suppressed_filter(a.value)
            for a in node.args
        ):
            issues.append(f"line {node.lineno}: deprecation warning suppression")
    return issues


def main() -> int:
    issues: list[str] = []
    for folder in ("custom_components/mennekes_amtron_rtu", "scripts", "tests"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            issues.extend(f"{path.relative_to(ROOT)}: {issue}" for issue in source_issues(path.read_text()))
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    issues.extend("pyproject.toml: deprecation warning suppression"
                  for value in config["tool"]["pytest"]["ini_options"].get("filterwarnings", [])
                  if suppressed_filter(value))
    issues.extend(f"{path.relative_to(ROOT)}: hidden runtime warnings"
                  for path in (ROOT / ".github/workflows").glob("*.yml")
                  if re.search(r"--disable-warnings|PYTHONWARNINGS[^\n]*ignore", path.read_text()))
    if issues:
        sys.stderr.write("\n".join(issues) + "\n")
        return 1
    sys.stdout.write("Owned API and deprecation checks passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
