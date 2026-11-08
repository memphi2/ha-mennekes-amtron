"""Explicit automatic vehicle selection with separately configured evidence."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.selector import SelectOptionDict

from ._options_estimates import EstimateOptionsFlow
from .config_schemas import (
    vehicle_detection_connected_states_schema,
    vehicle_detection_entities_schema,
    vehicle_detection_mode_schema,
    vehicle_detection_source_schema,
    vehicle_profile_choice_schema,
)
from .vehicle_profiles import (
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_VEHICLE_CONNECTED_ENTITY,
    CONF_VEHICLE_CONNECTED_STATES,
    CONF_VEHICLE_DETECTION_SOURCE,
    CONF_VEHICLE_DEVICE,
    CONF_VEHICLE_HOME_ENTITY,
    CONF_VEHICLE_PLUG_CHARGE,
    CONF_VEHICLE_PROFILE_ID,
    CONF_VEHICLE_PROFILE_SELECTED_AT,
    CONF_VEHICLE_PROFILES,
    CONF_VEHICLE_PROVIDER,
    CONF_VEHICLE_SOURCE_IDENTITY,
    VehicleDetectionConfig,
    VehicleProfile,
    active_profile,
    profiles,
    valid_connected_states,
    valid_detection_entity,
)

_DETECTION_KEYS = {
    CONF_VEHICLE_PROFILES,
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_PROFILE_SELECTED_AT,
}


class DetectionOptionsFlow(EstimateOptionsFlow):
    """Keep manual selection, detector setup and automatic mode independent."""

    async def async_step_vehicle_detection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._begin("detection", _DETECTION_KEYS)
        return self.async_show_menu(
            step_id="vehicle_detection",
            menu_options=[
                "vehicle_detection_mode",
                "vehicle_detection_profile",
                "back",
            ],
        )

    async def async_step_vehicle_detection_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("detection")) is None:
            return await self.async_step_init()
        configured = any(
            profile.detection is not None
            for profile in profiles(draft.values).values()
        )
        errors = {}
        if user_input is not None:
            enabled = user_input.get(CONF_VEHICLE_AUTO_SELECTION)
            if type(enabled) is not bool:
                errors[CONF_VEHICLE_AUTO_SELECTION] = "vehicle_detection_source_invalid"
            elif enabled and not configured:
                self._details["enable_detection"] = True
                if (profile := active_profile(draft.values)) is not None:
                    self._details["detection_profile"] = profile.id
                    return await self.async_step_vehicle_detection_source()
                return await self.async_step_vehicle_detection_profile()
            else:
                draft.values[CONF_VEHICLE_AUTO_SELECTION] = enabled
                return self._finish_section()
        return self.async_show_form(
            step_id="vehicle_detection_mode",
            data_schema=vehicle_detection_mode_schema(draft.values),
            errors=errors,
            last_step=configured,
        )

    async def async_step_vehicle_detection_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("detection")) is None:
            return await self.async_step_init()
        catalog = profiles(draft.values)
        errors = {}
        if user_input is not None:
            key = user_input.get(CONF_VEHICLE_PROFILE_ID)
            if not isinstance(key, str) or key not in catalog:
                errors[CONF_VEHICLE_PROFILE_ID] = "vehicle_profile_required"
            else:
                self._details["detection_profile"] = key
                return await self.async_step_vehicle_detection_source()
        choices: list[SelectOptionDict] = [
            {"value": profile.id, "label": profile.name} for profile in catalog.values()
        ]
        return self.async_show_form(
            step_id="vehicle_detection_profile",
            data_schema=vehicle_profile_choice_schema(
                choices, draft.values.get(CONF_ACTIVE_VEHICLE_PROFILE)
            ),
            errors=errors,
        )

    def _detection_profile(self) -> VehicleProfile | None:
        draft = self._section("detection")
        key = self._details.get("detection_profile")
        if draft is None or not isinstance(key, str):
            return None
        return profiles(draft.values).get(key)

    async def async_step_vehicle_detection_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (profile := self._detection_profile()) is None:
            return await self.async_step_init()
        capable = "detection" in self._vehicle_capabilities(
            {
                CONF_VEHICLE_PROVIDER: profile.provider_id,
                CONF_VEHICLE_DEVICE: profile.device_id,
                CONF_VEHICLE_SOURCE_IDENTITY: profile.identity,
            }
        )
        errors = {}
        if user_input is not None:
            source = user_input.get(CONF_VEHICLE_DETECTION_SOURCE)
            if source == "disabled":
                return self._save_detection(None)
            if source == "entities":
                self._details["detection_entities"] = True
                return await self.async_step_vehicle_detection_entities()
            if source == "profile":
                if capable:
                    return self._save_detection(VehicleDetectionConfig("profile"))
                errors[CONF_VEHICLE_DETECTION_SOURCE] = "vehicle_capability_unavailable"
            else:
                errors[CONF_VEHICLE_DETECTION_SOURCE] = (
                    "vehicle_detection_source_invalid"
                )
        return self.async_show_form(
            step_id="vehicle_detection_source",
            data_schema=vehicle_detection_source_schema(
                profile.detection.source
                if profile.detection is not None
                else ("profile" if capable else "entities")
                if self._details.get("enable_detection") else "disabled",
                profile=capable,
            ),
            description_placeholders={"vehicle": profile.name},
            errors=errors,
        )

    async def async_step_vehicle_detection_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (profile := self._detection_profile()) is None or not self._details.get(
            "detection_entities"
        ):
            return await self.async_step_init()
        current: dict[str, Any] = {}
        if profile.detection is not None and profile.detection.source == "entities":
            current = {
                CONF_VEHICLE_CONNECTED_ENTITY: profile.detection.connected_entity,
                CONF_VEHICLE_HOME_ENTITY: profile.detection.home_entity,
            }
        errors = {}
        if user_input is not None:
            current = user_input
            connected, home = (
                user_input.get(CONF_VEHICLE_CONNECTED_ENTITY),
                user_input.get(CONF_VEHICLE_HOME_ENTITY),
            )
            for key, value, home_source in (
                (CONF_VEHICLE_CONNECTED_ENTITY, connected, False),
                (CONF_VEHICLE_HOME_ENTITY, home, True),
            ):
                if not valid_detection_entity(value, home=home_source):
                    errors[key] = "vehicle_detection_entity_invalid"
            if not errors:
                if connected == home:
                    errors[CONF_VEHICLE_HOME_ENTITY] = (
                        "vehicle_detection_entities_distinct"
                    )
                elif cast(str, connected).startswith("sensor."):
                    self._details["detection_entities_chosen"] = (connected, home)
                    return await self.async_step_vehicle_detection_connected_states()
                else:
                    return self._save_detection(
                        VehicleDetectionConfig(
                            "entities", cast(str, connected), cast(str, home)
                        )
                    )
        return self.async_show_form(
            step_id="vehicle_detection_entities",
            description_placeholders={"vehicle": profile.name},
            data_schema=vehicle_detection_entities_schema(current),
            errors=errors,
            last_step=True,
        )

    async def async_step_vehicle_detection_connected_states(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        chosen = self._details.get("detection_entities_chosen")
        if (profile := self._detection_profile()) is None or chosen is None:
            return await self.async_step_init()
        connected, home = chosen
        previous = profile.detection
        current = list(
            previous.connected_states
            if previous is not None and previous.connected_entity == connected else ()
        )
        errors = {}
        if user_input is not None:
            value = user_input.get(CONF_VEHICLE_CONNECTED_STATES)
            states = (
                [item.strip() for item in value if isinstance(item, str)]
                if isinstance(value, list) else value
            )
            if isinstance(states, list):
                current = states
            if valid_connected_states(connected, states):
                return self._save_detection(
                    VehicleDetectionConfig("entities", connected, home, tuple(cast(list[str], states)))
                )
            errors[CONF_VEHICLE_CONNECTED_STATES] = "vehicle_connected_states_invalid"
        # Offer what the sensor itself reports; other values can be typed in.
        state = self.hass.states.get(connected)
        choices = list(current)
        if state is not None:
            reported = state.attributes.get("options")
            for item in [*(reported if isinstance(reported, list) else ()), state.state]:
                if (
                    isinstance(item, str) and item and item not in choices
                    and item not in ("unknown", "unavailable")
                ):
                    choices.append(item)
        return self.async_show_form(
            step_id="vehicle_detection_connected_states",
            description_placeholders={"vehicle": profile.name, "entity": connected},
            data_schema=vehicle_detection_connected_states_schema(choices, current),
            errors=errors,
            last_step=True,
        )

    def _save_detection(
        self, config: VehicleDetectionConfig | None
    ) -> ConfigFlowResult:
        draft = self._section("detection")
        profile = self._detection_profile()
        if draft is None or profile is None:
            raise RuntimeError("cannot save incomplete vehicle detection")
        raw_profile = draft.values[CONF_VEHICLE_PROFILES][profile.id]
        if config is None or profile.detection != config:
            raw_profile["options"].pop(CONF_VEHICLE_PLUG_CHARGE, None)
        if config is None:
            raw_profile.pop("detection", None)
        else:
            raw_profile["detection"] = config.as_dict()
        if self._details.get("enable_detection"):
            draft.values[CONF_VEHICLE_AUTO_SELECTION] = config is not None
        return self._finish_section()
