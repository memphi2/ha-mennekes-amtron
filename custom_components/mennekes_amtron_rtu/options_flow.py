"""Small, independently saved settings sections for one wallbox."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import valid_entity_id

from ._options_profiles import ProfileOptionsFlow
from .config_schemas import (
    advanced_schema,
    departure_calendar_schema,
    departure_capacity_schema,
    departure_configure_schema,
    departure_entity_schema,
    options_schema,
    vehicle_device_schema,
    vehicle_limit_schema,
    vehicle_profile_soc_schema,
    vehicle_soc_schema,
    vehicle_source_schema,
)
from .device.capabilities import phase_switch_is_supported
from .device.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PHASE_SWITCH_GRACE_SECONDS,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
)
from .remote import CONF_SERVER_URL
from .timed_goal_const import (
    CONF_TIMED_BAVARIAN_DEVICE,
    CONF_TIMED_CALENDAR_ENTITY,
    CONF_TIMED_CALENDAR_TITLE,
    CONF_TIMED_CAPACITY_ENTITY,
    CONF_TIMED_CAPACITY_KWH,
    CONF_TIMED_DEPARTURE_ENTITY,
    CONF_TIMED_GOAL_ENABLED,
    CONF_TIMED_GOAL_RESET_PENDING,
    CONF_TIMED_TIMER1_DAYS,
    CONF_TIMED_TIMER2_DAYS,
    CONF_TIMED_VEHICLE_TIME_ZONE,
    TIMED_WEEKDAYS,
)
from .vehicle_profiles import (
    CONF_TIMED_USE_VEHICLE_CAPACITY,
    CONF_VEHICLE_DEVICE,
    CONF_VEHICLE_LIMIT_USE_PROFILE,
    CONF_VEHICLE_PROVIDER,
    CONF_VEHICLE_SOC_USE_PROFILE,
    vehicle_soc_is_configured,
)
from .vehicle_soc_const import (
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_VEHICLE_SOC_ENTITY,
    VEHICLE_CHARGE_LIMIT_DOMAINS,
    VEHICLE_SOC_DOMAINS,
)
from .vehicle_sources import (
    provider_definitions,
    provider_device_is_valid,
    provider_selection,
)
from .vehicle_sources.const import (
    CONF_TIMED_VEHICLE_DEVICE,
    CONF_TIMED_VEHICLE_PROVIDER,
    PROVIDER_BAVARIANDATA,
    PROVIDER_NONE,
)

WALLBOX_KEYS = {
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PHASE_SWITCH_GRACE_SECONDS,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
}
VEHICLE_KEYS = {
    CONF_VEHICLE_SOC_ENTITY,
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_TIMED_GOAL_ENABLED,
    CONF_TIMED_GOAL_RESET_PENDING,
    CONF_VEHICLE_SOC_USE_PROFILE,
    CONF_VEHICLE_LIMIT_USE_PROFILE,
}
DEPARTURE_KEYS = {
    CONF_TIMED_GOAL_ENABLED,
    CONF_TIMED_GOAL_RESET_PENDING,
    CONF_TIMED_CAPACITY_ENTITY,
    CONF_TIMED_CAPACITY_KWH,
    CONF_TIMED_CALENDAR_ENTITY,
    CONF_TIMED_CALENDAR_TITLE,
    CONF_TIMED_DEPARTURE_ENTITY,
    CONF_TIMED_BAVARIAN_DEVICE,
    CONF_TIMED_VEHICLE_PROVIDER,
    CONF_TIMED_VEHICLE_DEVICE,
    CONF_TIMED_VEHICLE_TIME_ZONE,
    CONF_TIMED_TIMER1_DAYS,
    CONF_TIMED_TIMER2_DAYS,
    CONF_TIMED_USE_VEHICLE_CAPACITY,
}


class MennekesAmtronOptionsFlow(ProfileOptionsFlow):
    """Configure only the chosen feature, leaving all other options intact."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._draft = None
        self._details = {}
        self._profile_target = None
        self._end_wizard()
        # Departure planning and charging losses belong to a car's profile.
        return self.async_show_menu(
            step_id="init", menu_options=["wallbox", "vehicle", "costs"],
        )

    async def async_step_wallbox(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._begin("wallbox", WALLBOX_KEYS)
        choices = ["wallbox_basic"]
        if CONF_SERVER_URL not in self.config_entry.data:
            choices.append("wallbox_advanced")
        return self.async_show_menu(step_id="wallbox", menu_options=[*choices, "back"])

    async def async_step_wallbox_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("wallbox")) is None:
            return await self.async_step_init()
        service = CONF_SERVER_URL in self.config_entry.data
        if user_input is not None:
            draft.values.update(_normalized(user_input, service_mode=service))
            return await self._finish_section()
        return self.async_show_form(
            step_id="wallbox_basic",
            data_schema=options_schema(
                draft.values,
                max_current=_max_current(self.config_entry),
                service_mode=service,
            ),
            last_step=True,
        )

    async def async_step_wallbox_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            draft := self._section("wallbox")
        ) is None or CONF_SERVER_URL in self.config_entry.data:
            return await self.async_step_init()
        if user_input is not None:
            draft.values.update(
                {
                    key: int(user_input[key])
                    for key in (
                        CONF_MEASUREMENT_INTERVAL_SECONDS,
                        CONF_STATE_CHANGE_INTERVAL_SECONDS,
                        CONF_PHASE_SWITCH_GRACE_SECONDS,
                    )
                    if key in user_input
                }
            )
            return await self._finish_section()
        identity = getattr(
            getattr(self.config_entry, "runtime_data", None), "identity", None
        )
        phases = getattr(identity, "phase_options_hw", None)
        return self.async_show_form(
            step_id="wallbox_advanced",
            data_schema=advanced_schema(
                draft.values,
                phase_switch=phase_switch_is_supported(phases),
            ),
            last_step=True,
        )

    async def async_step_vehicle_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._target_ready():
            return await self.async_step_vehicle()
        draft = self._begin("vehicle", VEHICLE_KEYS)
        choices = [
            "vehicle_source" if draft.values.get(CONF_VEHICLE_PROVIDER) not in (None, "none") else "vehicle_soc"
        ]
        if vehicle_soc_is_configured(draft.values):
            choices.append("vehicle_disable")
        return self.async_show_menu(
            step_id="vehicle_battery", menu_options=self._area_menu(choices),
            description_placeholders=self._vehicle_placeholders(),
        )

    async def async_step_vehicle_source(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if (draft := self._section("vehicle")) is None or draft.values.get(CONF_VEHICLE_PROVIDER) in (None, "none"):
            return await self.async_step_init()
        capabilities = self._vehicle_capabilities(draft.values)
        errors = {}
        if user_input is not None:
            source = user_input.get("vehicle_soc_source")
            if source == "profile":
                if "soc" not in capabilities:
                    errors["vehicle_soc_source"] = "vehicle_capability_unavailable"
                else:
                    self._details["soc_profile"] = True
                    return await self.async_step_vehicle_profile_soc(
                        None if "charge_limit" in capabilities else {}
                    )
            elif source == "entity":
                return await self.async_step_vehicle_soc()
            else:
                errors["vehicle_soc_source"] = "vehicle_profile_invalid"
        return self.async_show_form(
            step_id="vehicle_source", data_schema=vehicle_source_schema(
                draft.values, profile="soc" in capabilities,
            ), errors=errors, description_placeholders=self._vehicle_placeholders(),
        )

    async def async_step_vehicle_profile_soc(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if (draft := self._section("vehicle")) is None or not self._details.get("soc_profile"):
            return await self.async_step_init()
        capabilities = self._vehicle_capabilities(draft.values)
        if "soc" not in capabilities or (
            "charge_limit" not in capabilities and (
                user_input is None or user_input.get("use_vehicle_limit", False) is not False
            )
        ):
            return await self.async_step_vehicle_source()
        errors = {}
        if user_input is not None:
            if type(user_input.get("use_vehicle_limit", False)) is not bool:
                errors["use_vehicle_limit"] = "vehicle_profile_invalid"
            else:
                draft.clear({CONF_VEHICLE_SOC_ENTITY, CONF_VEHICLE_CHARGE_LIMIT_ENTITY})
                draft.values[CONF_VEHICLE_SOC_USE_PROFILE] = True
                draft.values[CONF_VEHICLE_LIMIT_USE_PROFILE] = user_input.get("use_vehicle_limit", False)
                return await self._finish_section()
        return self.async_show_form(
            step_id="vehicle_profile_soc", data_schema=vehicle_profile_soc_schema(draft.values),
            description_placeholders=self._vehicle_placeholders(),
            errors=errors, last_step=True,
        )

    async def async_step_vehicle_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("vehicle")) is None:
            return await self.async_step_init()
        draft.clear({CONF_VEHICLE_SOC_ENTITY, CONF_VEHICLE_CHARGE_LIMIT_ENTITY, CONF_VEHICLE_SOC_USE_PROFILE, CONF_VEHICLE_LIMIT_USE_PROFILE})
        draft.values[CONF_TIMED_GOAL_ENABLED] = False
        draft.values[CONF_TIMED_GOAL_RESET_PENDING] = True
        return await self._finish_section()

    async def async_step_vehicle_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("vehicle")) is None:
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            errors = _vehicle_errors(user_input)
            if not user_input.get(CONF_VEHICLE_SOC_ENTITY):
                errors[CONF_VEHICLE_SOC_ENTITY] = "vehicle_soc_required"
            if not errors:
                draft.clear({CONF_VEHICLE_SOC_USE_PROFILE, CONF_VEHICLE_LIMIT_USE_PROFILE})
                draft.values[CONF_VEHICLE_SOC_ENTITY] = user_input[
                    CONF_VEHICLE_SOC_ENTITY
                ]
                if user_input.get("use_vehicle_limit") is True:
                    self._details["limit"] = True
                    return await self.async_step_vehicle_limit()
                draft.clear({CONF_VEHICLE_CHARGE_LIMIT_ENTITY})
                return await self._finish_section()
        return self.async_show_form(
            step_id="vehicle_soc",
            description_placeholders=self._vehicle_placeholders(),
            data_schema=vehicle_soc_schema(
                draft.values if user_input is None else user_input,
            ),
            errors=errors,
        )

    async def async_step_vehicle_limit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("vehicle")) is None or not self._details.get(
            "limit"
        ):
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            errors = _vehicle_errors(
                {
                    **user_input,
                    CONF_VEHICLE_SOC_ENTITY: draft.values[CONF_VEHICLE_SOC_ENTITY],
                }
            )
            if not user_input.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY):
                errors[CONF_VEHICLE_CHARGE_LIMIT_ENTITY] = "invalid_vehicle_entity"
            if not errors:
                draft.values[CONF_VEHICLE_CHARGE_LIMIT_ENTITY] = user_input[
                    CONF_VEHICLE_CHARGE_LIMIT_ENTITY
                ]
                return await self._finish_section()
        return self.async_show_form(
            step_id="vehicle_limit",
            description_placeholders=self._vehicle_placeholders(),
            data_schema=vehicle_limit_schema(
                draft.values if user_input is None else user_input,
            ),
            errors=errors,
            last_step=True,
        )

    async def async_step_departure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._target_ready():
            return await self.async_step_vehicle()
        draft = self._begin("departure", DEPARTURE_KEYS)
        if not vehicle_soc_is_configured(self._target_options()):
            return await self.async_step_departure_requires_vehicle()
        choices = ["departure_configure"]
        if draft.values.get(CONF_TIMED_GOAL_ENABLED) is True:
            choices.append("departure_disable")
        return self.async_show_menu(
            step_id="departure", menu_options=self._area_menu(choices),
            description_placeholders=self._vehicle_placeholders(),
        )

    async def async_step_departure_requires_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="departure_requires_vehicle",
            # The guided setup has visited the charge level just before.
            menu_options=self._area_menu([]) if self._wizard is not None
            else ["vehicle_battery", "vehicle_profile"],
            description_placeholders=self._vehicle_placeholders(),
        )

    async def async_step_departure_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("departure")) is None:
            return await self.async_step_init()
        draft.values[CONF_TIMED_GOAL_ENABLED] = False
        draft.values[CONF_TIMED_GOAL_RESET_PENDING] = True
        return await self._finish_section()

    async def async_step_departure_configure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("departure")) is None:
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            source = user_input.get("capacity_source")
            if source not in ("fixed", "entity", "profile"):
                errors["capacity_source"] = "capacity_source_invalid"
            elif not vehicle_soc_is_configured(self._target_options()):
                return await self.async_step_departure()
            elif source == "profile" and "capacity" not in self._vehicle_capabilities(draft.values):
                errors["capacity_source"] = "vehicle_capability_unavailable"
            else:
                self._details.update(user_input)
                self._details["remaining"] = [
                    step
                    for key, step in (
                        ("use_calendar", "departure_calendar"),
                        ("use_departure_entity", "departure_entity"),
                        ("use_vehicle_timer", "departure_vehicle"),
                    )
                    if user_input.get(key) is True
                ]
                if user_input.get("use_calendar") is not True:
                    draft.clear({CONF_TIMED_CALENDAR_ENTITY, CONF_TIMED_CALENDAR_TITLE})
                if user_input.get("use_departure_entity") is not True:
                    draft.clear({CONF_TIMED_DEPARTURE_ENTITY})
                if user_input.get("use_vehicle_timer") is not True:
                    draft.clear(
                        {
                            CONF_TIMED_BAVARIAN_DEVICE,
                            CONF_TIMED_VEHICLE_DEVICE,
                            CONF_TIMED_VEHICLE_TIME_ZONE,
                            CONF_TIMED_TIMER1_DAYS,
                            CONF_TIMED_TIMER2_DAYS,
                        }
                    )
                    draft.values[CONF_TIMED_VEHICLE_PROVIDER] = PROVIDER_NONE
                draft.values[CONF_TIMED_GOAL_ENABLED] = True
                if source == "profile":
                    draft.clear({CONF_TIMED_CAPACITY_ENTITY, CONF_TIMED_CAPACITY_KWH})
                    draft.values[CONF_TIMED_USE_VEHICLE_CAPACITY] = True
                    return await self._next_departure()
                draft.clear({CONF_TIMED_USE_VEHICLE_CAPACITY})
                if source == "fixed":
                    draft.clear({CONF_TIMED_CAPACITY_ENTITY})
                    return await self.async_step_departure_capacity_fixed()
                draft.clear({CONF_TIMED_CAPACITY_KWH})
                return await self.async_step_departure_capacity_entity()
        selection = provider_selection(draft.values, "departure")
        return self.async_show_form(
            step_id="departure_configure",
            description_placeholders=self._vehicle_placeholders(),
            data_schema=departure_configure_schema(
                draft.values if user_input is None else {**draft.values, **user_input},
                vehicle_timer=selection.provider_id != PROVIDER_NONE,
                profile_capacity="capacity" in self._vehicle_capabilities(draft.values),
            ),
            errors=errors,
        )

    async def async_step_departure_capacity_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._capacity(False, user_input)

    async def async_step_departure_capacity_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._capacity(True, user_input)

    async def _capacity(
        self, entity: bool, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if (draft := self._section("departure")) is None or self._details.get(
            "capacity_source"
        ) != ("entity" if entity else "fixed"):
            return await self.async_step_init()
        key = CONF_TIMED_CAPACITY_ENTITY if entity else CONF_TIMED_CAPACITY_KWH
        errors = {}
        if user_input is not None:
            errors = _timed_errors(user_input)
            if not user_input.get(key):
                errors[key] = (
                    "timed_source_invalid" if entity else "timed_capacity_invalid"
                )
            if not errors:
                draft.values[key] = user_input[key]
                return await self._next_departure()
        return self.async_show_form(
            step_id=f"departure_capacity_{'entity' if entity else 'fixed'}",
            description_placeholders=self._vehicle_placeholders(),
            data_schema=departure_capacity_schema(
                draft.values if user_input is None else user_input,
                entity=entity,
            ),
            errors=errors,
            last_step=not self._details["remaining"],
        )

    async def _next_departure(self) -> ConfigFlowResult:
        remaining = self._details["remaining"]
        if remaining:
            step = remaining.pop(0)
            self._details["departure_step"] = step
            if step == "departure_calendar":
                return await self.async_step_departure_calendar()
            if step == "departure_entity":
                return await self.async_step_departure_entity()
            return await self.async_step_departure_vehicle()
        return await self._finish_section()

    async def async_step_departure_calendar(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._departure_entity_fields("departure_calendar", user_input)

    async def async_step_departure_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._departure_entity_fields("departure_entity", user_input)

    async def _departure_entity_fields(
        self, step: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if (draft := self._section("departure")) is None or self._details.get(
            "departure_step"
        ) != step:
            return await self.async_step_init()
        calendar = step == "departure_calendar"
        keys = (
            (CONF_TIMED_CALENDAR_ENTITY, CONF_TIMED_CALENDAR_TITLE)
            if calendar
            else (CONF_TIMED_DEPARTURE_ENTITY,)
        )
        errors = {}
        if user_input is not None:
            errors = _timed_errors(user_input)
            for key in keys:
                if not user_input.get(key):
                    errors[key] = "timed_source_invalid"
            if not errors:
                draft.values.update({key: user_input[key] for key in keys})
                return await self._next_departure()
        schema = departure_calendar_schema if calendar else departure_entity_schema
        return self.async_show_form(
            step_id=step,
            description_placeholders=self._vehicle_placeholders(),
            data_schema=schema(draft.values if user_input is None else user_input),
            errors=errors,
            last_step=not self._details["remaining"],
        )

    async def async_step_departure_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("departure")) is None or self._details.get(
            "departure_step"
        ) != "departure_vehicle":
            return await self.async_step_init()
        if draft.values.get(CONF_VEHICLE_PROVIDER) not in (None, "none"):
            self._details["provider"] = "profile"
            self._details["suggested_device"] = draft.values.get(CONF_VEHICLE_DEVICE)
            return await self.async_step_departure_vehicle_details()
        return await self._async_vehicle_provider_step(
            values=draft.values, capability="departure",
            key=CONF_TIMED_VEHICLE_PROVIDER, step_id="departure_vehicle",
            user_input=user_input, next_step=self.async_step_departure_vehicle_details,
        )

    async def async_step_departure_vehicle_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            draft := self._section("departure")
        ) is None or "provider" not in self._details:
            return await self.async_step_init()
        profile = self._details["provider"] == "profile"
        provider_id = draft.values.get(CONF_VEHICLE_PROVIDER) if profile else self._details["provider"]
        provider = next(
            item
            for item in provider_definitions("departure")
            if item.id == provider_id
        )
        errors = {}
        if user_input is not None:
            device = draft.values.get(CONF_VEHICLE_DEVICE) if profile else user_input.get(CONF_TIMED_VEHICLE_DEVICE)
            if not provider_device_is_valid(self.hass, provider.id, device):
                errors[CONF_TIMED_VEHICLE_DEVICE] = "vehicle_device_invalid"
            errors.update(_timer_errors(user_input))
            if not errors:
                draft.clear(
                    {
                        CONF_TIMED_BAVARIAN_DEVICE,
                        CONF_TIMED_VEHICLE_TIME_ZONE,
                        CONF_TIMED_TIMER1_DAYS,
                        CONF_TIMED_TIMER2_DAYS,
                        CONF_TIMED_VEHICLE_DEVICE,
                    }
                )
                draft.values.update(
                    {
                        CONF_TIMED_VEHICLE_PROVIDER: "profile" if profile else provider.id,
                    }
                )
                if not profile:
                    draft.values[CONF_TIMED_VEHICLE_DEVICE] = device
                for key in (
                    CONF_TIMED_VEHICLE_TIME_ZONE,
                    CONF_TIMED_TIMER1_DAYS,
                    CONF_TIMED_TIMER2_DAYS,
                ):
                    if user_input.get(key):
                        draft.values[key] = user_input[key]
                return await self._next_departure()
        current = (
            {
                **draft.values,
                CONF_TIMED_VEHICLE_DEVICE: self._details["suggested_device"],
            }
            if user_input is None
            else user_input
        )
        return self.async_show_form(
            step_id="departure_vehicle_details",
            description_placeholders=self._vehicle_placeholders(),
            data_schema=vehicle_device_schema(
                current,
                key=CONF_TIMED_VEHICLE_DEVICE,
                integration=provider.integration_domain,
                timer_options=provider.id == PROVIDER_BAVARIANDATA,
                include_device=not profile,
            ),
            errors=errors,
            last_step=True,
        )

    async def _finish_section(self) -> ConfigFlowResult:
        """Recheck the vehicle requirement immediately before committing."""
        if (
            (draft := self._section("departure")) is not None
            and draft.values.get(CONF_TIMED_GOAL_ENABLED) is True
            and not vehicle_soc_is_configured(self._target_options())
        ):
            return self._options_changed()
        return await super()._finish_section()


def _normalized(user_input: dict[str, Any], *, service_mode: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        CONF_SCAN_INTERVAL_SECONDS: int(user_input[CONF_SCAN_INTERVAL_SECONDS]),
        CONF_CURRENT_LIMIT: float(user_input[CONF_CURRENT_LIMIT]),
    }
    if not service_mode:
        options[CONF_CONTROL_MODE] = str(user_input[CONF_CONTROL_MODE])
    return options


def _vehicle_errors(values: dict[str, Any]) -> dict[str, str]:
    errors = {}
    for key, domains in (
        (CONF_VEHICLE_SOC_ENTITY, VEHICLE_SOC_DOMAINS),
        (CONF_VEHICLE_CHARGE_LIMIT_ENTITY, VEHICLE_CHARGE_LIMIT_DOMAINS),
    ):
        value = values.get(key)
        if value and (
            not isinstance(value, str)
            or not valid_entity_id(value)
            or value.split(".", 1)[0] not in domains
        ):
            errors[key] = "invalid_vehicle_entity"
    return errors


def _timer_errors(values: dict[str, Any]) -> dict[str, str]:
    errors = {}
    for key in (CONF_TIMED_TIMER1_DAYS, CONF_TIMED_TIMER2_DAYS):
        days = values.get(key, [])
        if not isinstance(days, list) or any(day not in TIMED_WEEKDAYS for day in days):
            errors[key] = "timed_weekdays_invalid"
    if zone := values.get(CONF_TIMED_VEHICLE_TIME_ZONE):
        try:
            ZoneInfo(zone)
        except ValueError, TypeError, ZoneInfoNotFoundError:
            errors[CONF_TIMED_VEHICLE_TIME_ZONE] = "timed_timezone_invalid"
    return errors


def _timed_errors(values: dict[str, Any]) -> dict[str, str]:
    errors = {}
    for key, domains in (
        (CONF_TIMED_CAPACITY_ENTITY, ("sensor",)),
        (CONF_TIMED_DEPARTURE_ENTITY, ("sensor", "input_datetime")),
        (CONF_TIMED_CALENDAR_ENTITY, ("calendar",)),
    ):
        value = values.get(key)
        if value and (
            not isinstance(value, str)
            or not valid_entity_id(value)
            or value.split(".", 1)[0] not in domains
        ):
            errors[key] = "timed_source_invalid"
    if values.get(CONF_TIMED_CALENDAR_TITLE) and not values.get(
        CONF_TIMED_CALENDAR_ENTITY
    ):
        errors[CONF_TIMED_CALENDAR_TITLE] = "timed_calendar_required"
    if (
        values.get(CONF_TIMED_CALENDAR_ENTITY)
        and not str(values.get(CONF_TIMED_CALENDAR_TITLE, "")).strip()
    ):
        errors[CONF_TIMED_CALENDAR_TITLE] = "timed_calendar_title_required"
    capacity = values.get(CONF_TIMED_CAPACITY_KWH)
    if capacity is not None and (
        isinstance(capacity, bool)
        or not isinstance(capacity, (int, float))
        or not 1 <= capacity <= 250
    ):
        errors[CONF_TIMED_CAPACITY_KWH] = "timed_capacity_invalid"
    return errors


def _max_current(entry: Any) -> float:
    identity = getattr(getattr(entry, "runtime_data", None), "identity", None)
    maximum = getattr(identity, "max_evse_current", None)
    return (
        float(maximum) if isinstance(maximum, (int, float)) and maximum >= 6 else 16.0
    )


def control_mode_is_master(options: dict[str, Any]) -> bool:
    return options.get(CONF_CONTROL_MODE) == CONTROL_MODE_MASTER
