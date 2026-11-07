"""Small, independently saved settings sections for one wallbox."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import valid_entity_id

from ._options_estimates import EstimateOptionsFlow
from .config_schemas import (
    advanced_schema,
    departure_calendar_schema,
    departure_capacity_schema,
    departure_configure_schema,
    departure_entity_schema,
    options_schema,
    vehicle_device_schema,
    vehicle_limit_schema,
    vehicle_provider_schema,
    vehicle_soc_schema,
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
}


class MennekesAmtronOptionsFlow(EstimateOptionsFlow):
    """Configure only the chosen feature, leaving all other options intact."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._draft = None
        self._details = {}
        return self.async_show_menu(
            step_id="init",
            menu_options=["wallbox", "vehicle", "departure", "costs", "losses"],
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
            return self._review()
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
            return self._review()
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

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._begin("vehicle", VEHICLE_KEYS)
        return self.async_show_menu(
            step_id="vehicle", menu_options=["vehicle_soc", "vehicle_disable", "back"]
        )

    async def async_step_vehicle_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("vehicle")) is None:
            return await self.async_step_init()
        draft.clear({CONF_VEHICLE_SOC_ENTITY, CONF_VEHICLE_CHARGE_LIMIT_ENTITY})
        draft.values[CONF_TIMED_GOAL_ENABLED] = False
        draft.values[CONF_TIMED_GOAL_RESET_PENDING] = True
        return self._review()

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
                draft.values[CONF_VEHICLE_SOC_ENTITY] = user_input[
                    CONF_VEHICLE_SOC_ENTITY
                ]
                if user_input.get("use_vehicle_limit") is True:
                    self._details["limit"] = True
                    return await self.async_step_vehicle_limit()
                draft.clear({CONF_VEHICLE_CHARGE_LIMIT_ENTITY})
                return self._review()
        return self.async_show_form(
            step_id="vehicle_soc",
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
                return self._review()
        return self.async_show_form(
            step_id="vehicle_limit",
            data_schema=vehicle_limit_schema(
                draft.values if user_input is None else user_input,
            ),
            errors=errors,
            last_step=True,
        )

    async def async_step_departure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._begin("departure", DEPARTURE_KEYS)
        if not self.config_entry.options.get(CONF_VEHICLE_SOC_ENTITY):
            return await self.async_step_departure_requires_vehicle()
        return self.async_show_menu(
            step_id="departure",
            menu_options=["departure_configure", "departure_disable", "back"],
        )

    async def async_step_departure_requires_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="departure_requires_vehicle", menu_options=["vehicle", "back"]
        )

    async def async_step_departure_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("departure")) is None:
            return await self.async_step_init()
        draft.values[CONF_TIMED_GOAL_ENABLED] = False
        draft.values[CONF_TIMED_GOAL_RESET_PENDING] = True
        return self._review()

    async def async_step_departure_configure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("departure")) is None:
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            source = user_input.get("capacity_source")
            if source not in ("fixed", "entity"):
                errors["capacity_source"] = "capacity_source_invalid"
            elif not self.config_entry.options.get(CONF_VEHICLE_SOC_ENTITY):
                return await self.async_step_departure()
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
                if source == "fixed":
                    draft.clear({CONF_TIMED_CAPACITY_ENTITY})
                    return await self.async_step_departure_capacity_fixed()
                draft.clear({CONF_TIMED_CAPACITY_KWH})
                return await self.async_step_departure_capacity_entity()
        selection = provider_selection(draft.values, "departure")
        return self.async_show_form(
            step_id="departure_configure",
            data_schema=departure_configure_schema(
                draft.values if user_input is None else user_input,
                vehicle_timer=selection.provider_id != PROVIDER_NONE,
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
        return self._review()

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
        providers = provider_definitions("departure")
        selection = self._suggested_vehicle(draft.values, "departure")
        errors = {}
        if user_input is not None:
            provider = user_input.get(CONF_TIMED_VEHICLE_PROVIDER)
            if provider not in {item.id for item in providers}:
                errors[CONF_TIMED_VEHICLE_PROVIDER] = "vehicle_provider_invalid"
            else:
                self._details["provider"] = provider
                self._details["suggested_device"] = (
                    selection.device_id if selection.provider_id == provider else None
                )
                return await self.async_step_departure_vehicle_details()
        return self.async_show_form(
            step_id="departure_vehicle",
            data_schema=vehicle_provider_schema(
                CONF_TIMED_VEHICLE_PROVIDER,
                selection.provider_id,
                [item.id for item in providers],
            ),
            errors=errors,
        )

    async def async_step_departure_vehicle_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            draft := self._section("departure")
        ) is None or "provider" not in self._details:
            return await self.async_step_init()
        provider = next(
            item
            for item in provider_definitions("departure")
            if item.id == self._details["provider"]
        )
        errors = {}
        if user_input is not None:
            device = user_input.get(CONF_TIMED_VEHICLE_DEVICE)
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
                    }
                )
                draft.values.update(
                    {
                        CONF_TIMED_VEHICLE_PROVIDER: provider.id,
                        CONF_TIMED_VEHICLE_DEVICE: device,
                    }
                )
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
            data_schema=vehicle_device_schema(
                current,
                key=CONF_TIMED_VEHICLE_DEVICE,
                integration=provider.integration_domain,
                timer_options=provider.id == PROVIDER_BAVARIANDATA,
            ),
            errors=errors,
            last_step=True,
        )

    def _review(self) -> ConfigFlowResult:
        """Recheck the vehicle requirement immediately before committing."""
        if (
            (draft := self._section("departure")) is not None
            and draft.values.get(CONF_TIMED_GOAL_ENABLED) is True
            and not self.config_entry.options.get(CONF_VEHICLE_SOC_ENTITY)
        ):
            return self._options_changed()
        return super()._review()


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
