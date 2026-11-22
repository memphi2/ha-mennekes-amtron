"""The dashboard examples name entities that exist and leave no target out."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from homeassistant.util import slugify
from jinja2 import Environment, StrictUndefined

_INTEGRATION = Path("custom_components/mennekes_amtron_rtu")
_EXAMPLES = Path("examples/dashboard")
_LANGUAGES = ("de", "en", "fr", "it", "nl")
_TARGETS = (
    ("number", "energy_goal"), ("number", "cost_goal"), ("number", "soc_goal"),
    ("datetime", "departure_time"),
)


def _ids(language: str) -> dict[tuple[str, str], str]:
    names = json.loads((_INTEGRATION / "translations" / f"{language}.json").read_text(encoding="utf-8"))["entity"]
    return {
        (platform, key): f"{platform}.mennekes_amtron_{slugify(entry['name'])}"
        for platform, entities in names.items() for key, entry in entities.items()
    }


def _blank(node):
    """The card without its texts and IDs: what every language shares."""
    if isinstance(node, dict):
        return {key: _blank(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_blank(value) for value in node]
    return "" if isinstance(node, str) else node


@pytest.mark.parametrize("card", ["app", "basic"])
@pytest.mark.parametrize("language", _LANGUAGES)
def test_every_entity_exists_and_every_target_is_there(card, language):
    ids = _ids(language)
    text = (_EXAMPLES / f"{card}.{language}.yaml").read_text(encoding="utf-8")
    used = set(re.findall(r"\b[a-z_]+\.mennekes_amtron_[a-z0-9_]+", text))
    assert used <= set(ids.values()), used - set(ids.values())
    assert {ids[target] for target in _TARGETS} <= used


@pytest.mark.parametrize("card", ["app", "basic"])
def test_every_language_has_the_same_card(card):
    shapes = [
        _blank(yaml.safe_load((_EXAMPLES / f"{card}.{language}.yaml").read_text(encoding="utf-8")))
        for language in _LANGUAGES
    ]
    assert all(shape == shapes[0] for shape in shapes)


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_tile_names_a_cost_target_instead_of_the_energy_it_carries(language):
    ids = _ids(language)
    card = yaml.safe_load((_EXAMPLES / f"app.{language}.yaml").read_text(encoding="utf-8"))["cards"][0]
    values = {
        card["entity"]: "charging", ids["number", "cost_goal"]: "5.0", ids["number", "energy_goal"]: "12.3",
        ids["sensor", "current_estimated_charging_cost"]: "1.25",
    }
    environment = Environment(undefined=StrictUndefined, autoescape=True)
    environment.globals.update(
        states=lambda key, with_unit=False: values.get(key, "unknown") + (" €" if with_unit and key in values else ""),
        has_value=lambda key: key in values,
        is_state=lambda key, expected: values.get(key, "unknown") == expected,
        state_translated=lambda _: "Charging",
        state_attr=lambda _key, _name: None,
        state_attr_translated=lambda _key, _name: "",
    )
    secondary = environment.from_string(card["secondary"]).render(entity=card["entity"])
    assert "5.0 €" in secondary
    assert "1.25 €" in secondary
    assert "12.3" not in secondary
