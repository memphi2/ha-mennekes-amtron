"""Repair issues for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .entry_types import MennekesAmtronConfigEntry
from .repair_issues import (
    ISSUE_EMS_FALLBACK_NOT_CONFIGURED,
    ISSUE_EMS_HEARTBEAT_LOST,
    ISSUE_UNSUPPORTED_LAYOUT,
    ISSUE_WRITE_REJECTED,
    fallback_not_configured,
    heartbeat_lost,
    layout_placeholders,
    unsupported_layout,
    write_rejected,
)

LEARN_MORE_URL = (
    "https://github.com/memphi2/ha-mennekes-amtron/blob/main/docs/safety.md"
)


@callback
def async_setup_repairs(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> None:
    """Evaluate the repair issues now and on every coordinator update."""

    async_check_repairs(hass, entry)
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        coordinator.async_add_listener(lambda: async_check_repairs(hass, entry))
    )


@callback
def async_check_repairs(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> None:
    """Create or clear every repair issue of one entry."""

    runtime_data = entry.runtime_data
    data = runtime_data.coordinator.current_data()
    identity = runtime_data.identity

    _set_issue(
        hass,
        entry,
        ISSUE_EMS_HEARTBEAT_LOST,
        heartbeat_lost(data),
        severity=ir.IssueSeverity.ERROR,
        is_fixable=True,
    )
    _set_issue(
        hass,
        entry,
        ISSUE_EMS_FALLBACK_NOT_CONFIGURED,
        fallback_not_configured(data),
        severity=ir.IssueSeverity.WARNING,
    )
    _set_issue(
        hass,
        entry,
        ISSUE_UNSUPPORTED_LAYOUT,
        unsupported_layout(identity),
        severity=ir.IssueSeverity.WARNING,
        placeholders=layout_placeholders(identity),
    )
    _set_issue(
        hass,
        entry,
        ISSUE_WRITE_REJECTED,
        write_rejected(runtime_data.diagnostics),
        severity=ir.IssueSeverity.WARNING,
    )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the repair flow of a fixable issue."""

    entry_id = str((data or {}).get("entry_id", ""))
    return HeartbeatRecoveryRepairFlow(entry_id)


class HeartbeatRecoveryRepairFlow(RepairsFlow):
    """Run the documented recovery sequence for error state 200."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Show the confirmation step."""

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Write the recovery sequence once the user confirms."""

        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=None)

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None and hasattr(entry, "runtime_data"):
            await entry.runtime_data.control.async_recover_from_error()
        return self.async_create_entry(title="", data={})


def _issue_id(entry: MennekesAmtronConfigEntry, issue: str) -> str:
    return f"{entry.entry_id}_{issue}"


@callback
def _set_issue(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    issue: str,
    active: bool,
    *,
    severity: ir.IssueSeverity,
    is_fixable: bool = False,
    placeholders: dict[str, str] | None = None,
) -> None:
    issue_id = _issue_id(entry, issue)
    if not active:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        data={"entry_id": entry.entry_id, "issue": issue},
        is_fixable=is_fixable,
        issue_domain=DOMAIN,
        severity=severity,
        translation_key=issue,
        translation_placeholders=placeholders,
        learn_more_url=LEARN_MORE_URL,
    )
