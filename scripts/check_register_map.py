#!/usr/bin/env python3
"""Validate the register map against the entities and translations.

This is the gate the plan calls for: a register that gains an entity without
a translation, an enum value without a state string, a duplicate address or a
read block that drifted from the map all fail here instead of in a user's
log.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from check_reporting import report_failures

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "mennekes_amtron"
ENTITY_REFERENCE = ROOT / "docs" / "entities.md"
LANGUAGES = ("en", "de")

sys.path.insert(0, str(ROOT))


def main() -> int:
    """Run every register-map check."""

    failures: list[str] = []
    failures.extend(check_addresses())
    failures.extend(check_layouts())
    failures.extend(check_blocks())
    failures.extend(check_entity_translations())
    failures.extend(check_entity_icons())
    failures.extend(check_entity_reference())
    failures.extend(check_enum_states())
    return report_failures(failures, "Register map validation passed")


def check_addresses() -> list[str]:
    """Reject duplicate keys and overlapping register ranges."""

    from custom_components.mennekes_amtron.registers import REGISTERS

    failures: list[str] = []
    seen_keys: set[str] = set()
    occupied: dict[int, str] = {}
    for spec in REGISTERS:
        if spec.key in seen_keys:
            failures.append(f"duplicate register key: {spec.key}")
        seen_keys.add(spec.key)
        if spec.count < 1:
            failures.append(f"{spec.key} has a register count below one")
        for address in range(spec.address, spec.end_address):
            if address in occupied:
                failures.append(
                    f"register 0x{address:04X} is claimed by both "
                    f"{occupied[address]} and {spec.key}"
                )
            occupied[address] = spec.key
    return failures


def check_layouts() -> list[str]:
    """Reject register layout versions the specification does not define."""

    from custom_components.mennekes_amtron.registers import (
        LAYOUT_VERSIONS,
        REGISTERS,
    )

    return [
        f"{spec.key} declares the unknown layout 0x{spec.min_layout:04X}"
        for spec in REGISTERS
        if spec.min_layout not in LAYOUT_VERSIONS
    ]


def check_blocks() -> list[str]:
    """Reject read blocks that disagree with the register map."""

    from custom_components.mennekes_amtron.register_blocks import (
        block_consistency_failures,
    )

    return block_consistency_failures()


def check_entity_translations() -> list[str]:
    """Every entity key needs a name in every shipped language."""

    failures: list[str] = []
    entities = _entity_keys()
    for language, payload in _translation_payloads().items():
        section = payload.get("entity", {})
        for platform, keys in entities.items():
            translated = section.get(platform, {})
            for key in sorted(keys):
                if key not in translated:
                    failures.append(
                        f"{language}: entity {platform}.{key} has no translation"
                    )
                elif not translated[key].get("name"):
                    failures.append(
                        f"{language}: entity {platform}.{key} has no name"
                    )
    return failures


def check_entity_icons() -> list[str]:
    """An icon belongs where the device class does not already give one.

    Home Assistant derives an icon from the device class, and for several of
    them a state-dependent one: a plug shows connected or not, a problem shows
    alert or ok. Declaring an icon overrides that. The enum device class has
    no icon of its own, so those entities do need one.
    """

    icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
    section = icons.get("entity", {})
    failures: list[str] = []
    for platform, keys in _entity_keys().items():
        platform_icons = section.get(platform, {})
        with_icon_class = _device_classes_with_icon()[platform]
        for key in sorted(keys):
            declared = key in platform_icons
            if key in with_icon_class and declared:
                failures.append(
                    f"entity {platform}.{key} declares an icon although its "
                    f"device class {with_icon_class[key]!r} provides one"
                )
            elif key not in with_icon_class and not declared:
                failures.append(f"entity {platform}.{key} has no icon")
    return failures


def _device_classes_with_icon() -> dict[str, dict[str, str]]:
    """Return every entity whose device class already carries an icon."""

    from custom_components.mennekes_amtron.binary_sensor_descriptions import (
        BINARY_SENSOR_DESCRIPTIONS,
    )
    from custom_components.mennekes_amtron.sensor_descriptions import (
        SENSOR_DESCRIPTIONS,
    )

    classes: dict[str, dict[str, str]] = {
        platform: {} for platform in _entity_keys()
    }
    for platform, descriptions in (
        ("sensor", SENSOR_DESCRIPTIONS),
        ("binary_sensor", BINARY_SENSOR_DESCRIPTIONS),
    ):
        for description in descriptions:
            device_class = description.device_class
            # The enum device class has no icon of its own.
            if device_class is not None and device_class.value != "enum":
                classes[platform][description.key] = str(device_class.value)
    classes["number"]["charging_current_limit"] = "current"
    classes["switch"]["charging_release"] = "switch"
    classes["button"]["restart"] = "restart"
    return classes


def check_entity_reference() -> list[str]:
    """Every entity has to be described in docs/entities.md.

    The reference is what a user reads to find out what an entity means; an
    entity that is not in it is an entity nobody can use on purpose.
    """

    reference = ENTITY_REFERENCE.read_text(encoding="utf-8")
    return [
        f"docs/entities.md does not describe {platform}.{key}"
        for platform, keys in _entity_keys().items()
        for key in sorted(keys)
        if f"`{platform}.*_{key}`" not in reference
    ]


def check_enum_states() -> list[str]:
    """Every enum option of an entity needs a state string in every language."""

    failures: list[str] = []
    expected = _enum_states()
    for language, payload in _translation_payloads().items():
        section = payload.get("entity", {})
        for (platform, key), options in expected.items():
            states = section.get(platform, {}).get(key, {}).get("state", {})
            missing = sorted(set(options) - set(states))
            extra = sorted(set(states) - set(options))
            if missing:
                failures.append(
                    f"{language}: {platform}.{key} misses state(s) "
                    + ", ".join(missing)
                )
            if extra:
                failures.append(
                    f"{language}: {platform}.{key} has unknown state(s) "
                    + ", ".join(extra)
                )
    return failures


def _entity_keys() -> dict[str, set[str]]:
    from custom_components.mennekes_amtron.binary_sensor_descriptions import (
        BINARY_SENSOR_DESCRIPTIONS,
    )
    from custom_components.mennekes_amtron.sensor_descriptions import (
        SENSOR_DESCRIPTIONS,
    )

    return {
        "sensor": {description.key for description in SENSOR_DESCRIPTIONS},
        "binary_sensor": {
            description.key for description in BINARY_SENSOR_DESCRIPTIONS
        },
        "number": {"charging_current_limit"},
        "switch": {"charging_paused", "charging_release", "lock_evse"},
        "select": {"solar_charging_mode", "requested_phases"},
        "button": {"recover_from_error", "restart"},
    }


def _enum_states() -> dict[tuple[str, str], tuple[str, ...]]:
    from custom_components.mennekes_amtron.enums import (
        RequestedPhases,
        SolarChargingMode,
        enum_options,
    )
    from custom_components.mennekes_amtron.sensor_descriptions import (
        SENSOR_DESCRIPTIONS,
    )

    expected: dict[tuple[str, str], tuple[str, ...]] = {
        ("select", "solar_charging_mode"): enum_options(SolarChargingMode),
        ("select", "requested_phases"): enum_options(RequestedPhases),
    }
    for description in SENSOR_DESCRIPTIONS:
        if description.enum_class is not None:
            expected[("sensor", description.key)] = enum_options(
                description.enum_class
            )
    return expected


def _translation_payloads() -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {
        language: json.loads(
            (COMPONENT / "translations" / f"{language}.json").read_text(
                encoding="utf-8"
            )
        )
        for language in LANGUAGES
    }
    payloads["strings"] = json.loads(
        (COMPONENT / "strings.json").read_text(encoding="utf-8")
    )
    return payloads


if __name__ == "__main__":
    sys.exit(main())
