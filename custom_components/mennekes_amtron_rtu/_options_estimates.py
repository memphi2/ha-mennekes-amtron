"""Independent cost and loss configuration sections."""

from __future__ import annotations

import math
from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import valid_entity_id

from ._options_base import SectionOptionsFlow
from ._options_draft import OptionsDraft
from .charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_LOSS_AUTO_MATCH,
    CONF_LOSS_BAVARIAN_DEVICE,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
)
from .charging_cost_const import (
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_NETWORK_MODULE,
    CONF_CHARGING_NETWORK_TARIFF,
    CONF_CHARGING_PRICE,
    CONF_CHARGING_PRICE_ENTITY,
    CONF_HIGH_NETWORK,
    CONF_LOW_NETWORK,
    CONF_PRICE_INCLUDES_NETWORK,
    CONF_REGULAR_NETWORK,
    CONF_TARIFF_YEAR,
    COST_SOURCE_DISABLED,
    COST_SOURCE_FIXED,
    COST_SOURCE_SENSOR,
    NETWORK_MODULE_3,
    NETWORK_MODULE_NONE,
    NETWORK_MODULES,
)
from .config_schemas import (
    cost_kind_schema,
    cost_module_schema,
    cost_module_three_schema,
    cost_network_schema,
    cost_source_schema,
    loss_sensor_schema,
    vehicle_device_schema,
)
from .network_tariff_config import parse_tariff_inputs, tariff_form_values
from .network_tariff_lookup import async_lookup_home_tariff
from .vehicle_profiles import (
    CONF_VEHICLE_PROFILES,
    CONF_VEHICLE_PROVIDER,
    active_profile,
)
from .vehicle_sources import (
    provider_definitions,
    provider_device_is_valid,
)
from .vehicle_sources.const import (
    CONF_LOSS_VEHICLE_DEVICE,
    CONF_LOSS_VEHICLE_PROVIDER,
)

COST_KEYS = {
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_PRICE,
    CONF_CHARGING_PRICE_ENTITY,
    CONF_CHARGING_NETWORK_TARIFF,
    CONF_AUTO_TARIFF_REFRESH,
}
LOSS_KEYS = {
    CONF_CHARGE_LOSS_SOURCE,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
    CONF_LOSS_AUTO_MATCH,
    CONF_LOSS_BAVARIAN_DEVICE,
    CONF_LOSS_VEHICLE_PROVIDER,
    CONF_LOSS_VEHICLE_DEVICE,
}


class EstimateOptionsFlow(SectionOptionsFlow):
    """Each estimate can be configured without editing the other features."""

    async def async_step_costs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        draft = self._begin("costs", COST_KEYS)
        errors = {}
        if user_input is not None:
            source = user_input.get(CONF_CHARGING_COST_SOURCE)
            if source == COST_SOURCE_DISABLED:
                draft.clear(COST_KEYS)
                return self._finish_section()
            if source in (COST_SOURCE_FIXED, COST_SOURCE_SENSOR):
                self._details["cost_source"] = source
                return await self._cost_source(source, None)
            errors[CONF_CHARGING_COST_SOURCE] = "cost_source_invalid"
        return self.async_show_form(
            step_id="costs", data_schema=cost_kind_schema(draft.values), errors=errors,
        )

    async def async_step_cost_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._cost_source(COST_SOURCE_FIXED, user_input)

    async def async_step_cost_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._cost_source(COST_SOURCE_SENSOR, user_input)

    async def _cost_source(
        self, source: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if (draft := self._section("costs")) is None or self._details.get("cost_source") != source:
            return await self.async_step_init()
        allow_network = getattr(self.hass.config, "country", None) == "DE"
        errors = _cost_errors(source, user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            draft.values[CONF_CHARGING_COST_SOURCE] = source
            key = (
                CONF_CHARGING_PRICE
                if source == COST_SOURCE_FIXED
                else CONF_CHARGING_PRICE_ENTITY
            )
            other = (
                CONF_CHARGING_PRICE_ENTITY
                if source == COST_SOURCE_FIXED
                else CONF_CHARGING_PRICE
            )
            draft.values[key] = user_input[key]
            draft.clear({other})
            previous = draft.values.get(CONF_CHARGING_NETWORK_TARIFF)
            self._details["tariff"] = tariff_form_values(
                previous if isinstance(previous, dict) else {}
            )
            self._details["tariff"][CONF_AUTO_TARIFF_REFRESH] = draft.values.get(
                CONF_AUTO_TARIFF_REFRESH, False
            )
            self._details["source"] = "—"
            self._details["cost_ready"] = True
            if not allow_network:
                draft.values[CONF_CHARGING_NETWORK_TARIFF] = {
                    "module": NETWORK_MODULE_NONE
                }
                draft.clear({CONF_AUTO_TARIFF_REFRESH})
                return self._finish_section()
            return await self.async_step_cost_network()
        current = dict(draft.values) if user_input is None else user_input
        return self.async_show_form(
            step_id=f"cost_{source}",
            data_schema=cost_source_schema(current, source=source),
            errors=errors,
            last_step=not allow_network,
        )

    async def async_step_cost_network(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            (draft := self._section("costs")) is None
            or not self._details.get("cost_ready")
            or getattr(self.hass.config, "country", None) != "DE"
        ):
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            module = user_input.get(CONF_CHARGING_NETWORK_MODULE)
            if module not in NETWORK_MODULES:
                errors[CONF_CHARGING_NETWORK_MODULE] = "network_tariff_invalid"
            else:
                self._details["module"] = module
                if module == NETWORK_MODULE_NONE:
                    draft.values[CONF_CHARGING_NETWORK_TARIFF] = {"module": NETWORK_MODULE_NONE}
                    draft.clear({CONF_AUTO_TARIFF_REFRESH})
                    return self._finish_section()
                if module == NETWORK_MODULE_3:
                    return await self.async_step_cost_module3_source()
                return await self.async_step_cost_module2()
        return self.async_show_form(
            step_id="cost_network",
            data_schema=cost_module_schema(draft.values),
            errors=errors,
        )

    async def async_step_cost_module2(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("costs")) is None or self._details.get(
            "module"
        ) != "module_2":
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            try:
                tariff = parse_tariff_inputs(
                    user_input, module="module_2", timezone="Europe/Berlin"
                )
            except KeyError, TypeError, ValueError:
                errors["base"] = "network_tariff_invalid"
            else:
                return self._finish_tariff(draft, tariff)
        return self.async_show_form(
            step_id="cost_module2",
            data_schema=cost_network_schema(
                self._details["tariff"] if user_input is None else user_input,
                module="module_2",
            ),
            errors=errors,
            last_step=True,
        )

    async def async_step_cost_module3_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            self._section("costs") is None
            or self._details.get("module") != NETWORK_MODULE_3
        ):
            return await self.async_step_init()
        return self.async_show_menu(
            step_id="cost_module3_source",
            menu_options=["cost_module3_manual", "cost_module3_import", "back"],
        )

    async def async_step_cost_module3_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._details["lookup_failed"] = False
        return await self.async_step_cost_module3_prices()

    async def async_step_cost_module3_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            self._section("costs") is None
            or self._details.get("module") != NETWORK_MODULE_3
        ):
            return await self.async_step_init()
        values = await async_lookup_home_tariff(self.hass)
        self._details["lookup_failed"] = values is None
        if values is not None:
            self._details["tariff"].update(
                {
                    key: value
                    for key, value in values.items()
                    if not key.startswith("_source_")
                }
            )
            prefix = "⚠ " if values.get("_source_status") == "vorlaeufig" else ""
            self._details["source"] = prefix + str(values.get("_source_url") or "—")
        return await self.async_step_cost_module3_prices()

    async def async_step_cost_module3_prices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            (draft := self._section("costs")) is None
            or self._details.get("module") != NETWORK_MODULE_3
        ):
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            errors = _network_price_errors(user_input)
            if not errors:
                try:
                    tariff = parse_tariff_inputs(
                        user_input, module=NETWORK_MODULE_3, timezone="Europe/Berlin"
                    )
                except KeyError, TypeError, ValueError:
                    errors["base"] = "network_tariff_invalid"
                else:
                    return self._finish_tariff(
                        draft, tariff,
                        automatic=user_input.get(CONF_AUTO_TARIFF_REFRESH, False),
                    )
        elif self._details.get("lookup_failed"):
            errors["base"] = "tariff_lookup_failed"
        return self.async_show_form(
            step_id="cost_module3_prices",
            data_schema=cost_module_three_schema(self._details["tariff"] if user_input is None else user_input),
            errors=errors,
            description_placeholders={"source": self._details["source"]},
            last_step=True,
        )

    def _finish_tariff(
        self, draft: OptionsDraft, tariff: dict[str, Any], *, automatic: bool = False
    ) -> ConfigFlowResult:
        draft.values[CONF_CHARGING_NETWORK_TARIFF] = tariff
        draft.clear({CONF_AUTO_TARIFF_REFRESH})
        if tariff["module"] == NETWORK_MODULE_3:
            draft.values[CONF_AUTO_TARIFF_REFRESH] = automatic
        return self._finish_section()

    async def async_step_losses(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        draft = self._begin("losses", LOSS_KEYS)
        choices = ["loss_sensor"]
        if draft.values.get(CONF_VEHICLE_PROVIDER) not in (None, "none"):
            choices.append("loss_profile")
        elif CONF_VEHICLE_PROFILES not in self.config_entry.options or self._suggested_vehicle(draft.values, "charge_history").provider_id != "none":
            choices.append("loss_vehicle")
        choices.append("loss_disable")
        if CONF_VEHICLE_PROFILES in self.config_entry.options and active_profile(self.config_entry.options) is None:
            choices = ["vehicle_profiles"]
        return self.async_show_menu(
            step_id="losses",
            menu_options=[*choices, "back"],
        )

    async def async_step_loss_profile(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if (draft := self._section("losses")) is None:
            return await self.async_step_init()
        if "charge_history" not in self._vehicle_capabilities(draft.values):
            return self.async_show_form(
                step_id="loss_profile", data_schema=probatio.Schema({}),
                errors={"base": "vehicle_capability_unavailable"},
            )
        draft.clear(LOSS_KEYS)
        draft.values[CONF_CHARGE_LOSS_SOURCE] = "vehicle_history"
        draft.values[CONF_LOSS_VEHICLE_PROVIDER] = "profile"
        return self._finish_section()

    async def async_step_loss_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("losses")) is None:
            return await self.async_step_init()
        draft.clear(LOSS_KEYS)
        return self._finish_section()

    async def async_step_loss_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("losses")) is None:
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            entity = user_input.get(CONF_VEHICLE_LAST_CHARGE_ENERGY)
            if (
                not isinstance(entity, str)
                or not valid_entity_id(entity)
                or not entity.startswith("sensor.")
            ):
                errors[CONF_VEHICLE_LAST_CHARGE_ENERGY] = "loss_source_invalid"
            if type(user_input.get(CONF_LOSS_AUTO_MATCH, False)) is not bool:
                errors[CONF_LOSS_AUTO_MATCH] = "loss_source_invalid"
            if not errors:
                draft.clear(LOSS_KEYS)
                draft.values.update(
                    {
                        CONF_CHARGE_LOSS_SOURCE: "sensor",
                        CONF_VEHICLE_LAST_CHARGE_ENERGY: entity,
                        CONF_LOSS_AUTO_MATCH: user_input.get(
                            CONF_LOSS_AUTO_MATCH, False
                        ),
                    }
                )
                return self._finish_section()
        return self.async_show_form(
            step_id="loss_sensor",
            data_schema=loss_sensor_schema(
                draft.values if user_input is None else user_input,
            ),
            errors=errors,
            last_step=True,
        )

    async def async_step_loss_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("losses")) is None:
            return await self.async_step_init()
        return await self._async_vehicle_provider_step(
            values=draft.values, capability="charge_history",
            key=CONF_LOSS_VEHICLE_PROVIDER, step_id="loss_vehicle",
            user_input=user_input, next_step=self.async_step_loss_vehicle_details,
        )

    async def async_step_loss_vehicle_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            draft := self._section("losses")
        ) is None or "provider" not in self._details:
            return await self.async_step_init()
        provider = next(
            item
            for item in provider_definitions("charge_history")
            if item.id == self._details["provider"]
        )
        errors = {}
        if user_input is not None:
            device = user_input.get(CONF_LOSS_VEHICLE_DEVICE)
            if not provider_device_is_valid(self.hass, provider.id, device):
                errors[CONF_LOSS_VEHICLE_DEVICE] = "vehicle_device_invalid"
            else:
                draft.clear(LOSS_KEYS)
                draft.values.update(
                    {
                        CONF_CHARGE_LOSS_SOURCE: "vehicle_history",
                        CONF_LOSS_VEHICLE_PROVIDER: provider.id,
                        CONF_LOSS_VEHICLE_DEVICE: device,
                    }
                )
                return self._finish_section()
        current = (
            {CONF_LOSS_VEHICLE_DEVICE: self._details["suggested_device"]}
            if user_input is None
            else user_input
        )
        return self.async_show_form(
            step_id="loss_vehicle_details",
            data_schema=vehicle_device_schema(
                current,
                key=CONF_LOSS_VEHICLE_DEVICE,
                integration=provider.integration_domain,
            ),
            errors=errors,
            last_step=True,
        )


def _network_price_errors(values: dict[str, Any]) -> dict[str, str]:
    try:
        prices = [
            values[key]
            for key in (CONF_LOW_NETWORK, CONF_REGULAR_NETWORK, CONF_HIGH_NETWORK)
        ]
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in prices
        ):
            raise ValueError
        low, regular, high = prices
        year = values[CONF_TARIFF_YEAR]
        if (
            not 0 <= low < regular < high <= 1000
            or type(year) is not int
            or not 2024 <= year <= 9998
        ):
            raise ValueError
        if (
            type(values[CONF_PRICE_INCLUDES_NETWORK]) is not bool
            or type(values.get(CONF_AUTO_TARIFF_REFRESH, False)) is not bool
        ):
            raise ValueError
    except KeyError, TypeError, ValueError:
        return {"base": "network_tariff_invalid"}
    return {}


def _cost_errors(
    source: str,
    values: dict[str, Any],
) -> dict[str, str]:
    errors = {}
    if source == COST_SOURCE_FIXED:
        value = values.get(CONF_CHARGING_PRICE)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or abs(value) > 1000
            or not math.isfinite(value)
        ):
            errors[CONF_CHARGING_PRICE] = "cost_price_invalid"
    else:
        value = values.get(CONF_CHARGING_PRICE_ENTITY)
        if (
            not isinstance(value, str)
            or not valid_entity_id(value)
            or not value.startswith("sensor.")
        ):
            errors[CONF_CHARGING_PRICE_ENTITY] = "cost_source_invalid"
    return errors
