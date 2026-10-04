"""Meta tests: the shipped translations have to stay complete."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "mennekes_amtron"
LANGUAGES = ("en", "de")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(payload: Any, prefix: str = "") -> set[str]:
    if not isinstance(payload, dict):
        return {prefix}
    keys: set[str] = set()
    for key, value in payload.items():
        keys |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return keys


def test_strings_and_english_translations_are_identical() -> None:
    assert _load(COMPONENT / "strings.json") == _load(
        COMPONENT / "translations" / "en.json"
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_has_the_same_keys(language: str) -> None:
    reference = _keys(_load(COMPONENT / "strings.json"))
    translated = _keys(_load(COMPONENT / "translations" / f"{language}.json"))
    assert translated == reference


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_translation_is_empty(language: str) -> None:
    payload = _load(COMPONENT / "translations" / f"{language}.json")
    empty = sorted(key for key in _walk(payload) if not key[1].strip())
    assert empty == []


def test_every_exception_translation_key_is_used() -> None:
    declared = set(_load(COMPONENT / "strings.json")["exceptions"])
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in COMPONENT.glob("*.py")
    )
    unused = sorted(key for key in declared if f'"{key}"' not in sources)
    assert unused == []


def test_every_raised_translation_key_is_declared() -> None:
    import re

    declared = set(_load(COMPONENT / "strings.json")["exceptions"])
    pattern = re.compile(
        r"(?:service_validation_error|home_assistant_error)\(\s*\n?\s*\"([a-z_]+)\""
    )
    raised: set[str] = set()
    for path in COMPONENT.glob("*.py"):
        raised |= set(pattern.findall(path.read_text(encoding="utf-8")))
    assert raised
    assert raised <= declared


def test_the_repair_issues_are_translated() -> None:
    from custom_components.mennekes_amtron import repair_issues

    declared = set(_load(COMPONENT / "strings.json")["issues"])
    expected = {
        repair_issues.ISSUE_EMS_HEARTBEAT_LOST,
        repair_issues.ISSUE_EMS_FALLBACK_NOT_CONFIGURED,
        repair_issues.ISSUE_UNSUPPORTED_LAYOUT,
        repair_issues.ISSUE_WRITE_REJECTED,
    }
    assert declared == expected


def test_the_action_fields_match_the_service_description() -> None:
    import yaml  # noqa: PLC0415 - only needed for this meta test

    services = yaml.safe_load(
        (COMPONENT / "services.yaml").read_text(encoding="utf-8")
    )
    strings = _load(COMPONENT / "strings.json")["services"]
    assert set(services) == set(strings)
    for action, definition in services.items():
        assert set(definition["fields"]) == set(strings[action]["fields"])


def _walk(payload: Any, prefix: str = ""):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from _walk(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(payload, str):
        yield (prefix, payload)
