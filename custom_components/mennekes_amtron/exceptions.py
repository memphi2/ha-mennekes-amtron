"""Translated Home Assistant exceptions for MENNEKES AMTRON."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN


def service_validation_error(
    translation_key: str,
    placeholders: dict[str, str] | None = None,
) -> ServiceValidationError:
    """Create a translated service validation error."""

    return ServiceValidationError(**_kwargs(translation_key, placeholders))


def home_assistant_error(
    translation_key: str,
    placeholders: dict[str, str] | None = None,
) -> HomeAssistantError:
    """Create a translated runtime error."""

    return HomeAssistantError(**_kwargs(translation_key, placeholders))


def _kwargs(
    translation_key: str,
    placeholders: dict[str, str] | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "translation_domain": DOMAIN,
        "translation_key": translation_key,
    }
    if placeholders:
        kwargs["translation_placeholders"] = placeholders
    return kwargs
