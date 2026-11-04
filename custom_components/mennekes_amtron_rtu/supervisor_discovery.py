"""Read app connection details from the authenticated local Supervisor API."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .device.const import DEFAULT_NAME, DOMAIN
from .device.service_discovery import APP_HOSTNAME


async def async_app_discoveries(hass: HomeAssistant) -> list[HassioServiceInfo]:
    """Find this app without requiring Supervisor on standalone HA installs."""
    if "hassio" not in hass.config.components:
        return []
    # These optional dependencies exist only when HA has loaded hassio.
    from aiohasupervisor import SupervisorError
    from homeassistant.components.hassio.handler import get_supervisor_client

    try:
        discoveries = await get_supervisor_client(hass).discovery.list()
    except SupervisorError:
        return []
    return [
        HassioServiceInfo(
            config=item.config, name=DEFAULT_NAME, slug=item.addon, uuid=item.uuid.hex
        )
        for item in discoveries
        if item.service == DOMAIN
        and APP_HOSTNAME.fullmatch(item.addon.replace("_", "-"))
    ]
