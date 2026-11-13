"""New control/diagnostic features expose complete states in every UI language."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.mennekes_amtron_rtu.device.charging_status import (
    CHARGING_STATUS_OPTIONS,
)
from custom_components.mennekes_amtron_rtu.device.energy_goal import ENERGY_GOAL_STATES
from custom_components.mennekes_amtron_rtu.device.link_quality import (
    CONNECTION_QUALITY_STATES,
)
from custom_components.mennekes_amtron_rtu.device.soc_goal import SOC_GOAL_STATES

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "mennekes_amtron_rtu"


def test_new_status_options_and_control_errors_are_translated_in_all_languages() -> None:
    for path in [COMPONENT / "strings.json", *sorted((COMPONENT / "translations").glob("*.json"))]:
        payload = json.loads(path.read_text())
        entity = payload["entity"]
        for key, options in (
            ("charging_status", CHARGING_STATUS_OPTIONS),
            ("connection_quality", CONNECTION_QUALITY_STATES),
            ("energy_goal_status", ENERGY_GOAL_STATES),
            ("soc_goal_status", SOC_GOAL_STATES),
        ):
            translated = entity["sensor"][key]
            assert translated["name"], (path.name, key)
            assert set(translated["state"]) == set(options), (path.name, key)
            assert all(translated["state"].values()), (path.name, key)
        assert entity["number"]["energy_goal"]["name"]
        assert entity["number"]["soc_goal"]["name"]
        assert entity["button"]["cancel_pending_change"]["name"]
        for key in (
            "energy_goal_invalid", "energy_goal_unavailable", "energy_goal_stop_active", "service_job_active",
            "energy_goal_state_invalid", "energy_goal_not_supported", "cancel_pending_not_supported",
            "soc_goal_invalid", "soc_goal_unavailable", "soc_goal_source_changed",
            "soc_goal_state_invalid", "soc_goal_not_supported", "soc_goal_limit_unavailable",
        ):
            assert payload["exceptions"][key]["message"], (path.name, key)
        for step, key in (
            ("vehicle_soc", "vehicle_soc_entity"),
            ("vehicle_limit", "vehicle_charge_limit_entity"),
        ):
            form = payload["options"]["step"][step]
            assert form["data"][key]
            assert form["data_description"][key]
        reasons = entity["event"]["control_result"]["state_attributes"]["reason"]["state"]
        assert reasons["user_cancelled"]
        assert reasons["energy_goal"]
