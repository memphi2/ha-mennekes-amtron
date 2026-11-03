"""Repair issues for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir

from .client_errors import AmtronBusError
from .const import DOMAIN, ERROR_CODE_EMS_UNAVAILABLE
from .entry_types import MennekesAmtronConfigEntry
from .registers import ERROR_CODE
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
    """Evaluate repairs now and when coordinator data or availability changes.

    Control errors also call async_check_repairs directly: a failed write need
    not change any polled register, and identical polls do not notify listeners.
    """

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
    ) -> RepairsFlowResult:
        """Show the confirmation step."""

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Write the recovery sequence once the user confirms."""

        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=None)

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_create_entry(data={})
        if not hasattr(entry, "runtime_data"):
            return self._show_error("entry_unavailable")
        runtime = entry.runtime_data
        try:
            # The error may already have cleared while the dialog was open.
            # Acknowledging a stale issue must not send another recovery.
            error = await runtime.client.async_read_register(ERROR_CODE)
            if error == 0:
                runtime.repair_state[ISSUE_EMS_HEARTBEAT_LOST] = None
                return self.async_create_entry(data={})
            if error != ERROR_CODE_EMS_UNAVAILABLE:
                return self._show_error("different_error")
            await runtime.control.async_recover_from_error()
            error = await runtime.client.async_read_register(ERROR_CODE)
        except ServiceValidationError as err:
            return self._show_error(
                "read_only" if err.translation_key == "read_only_mode"
                else "write_rate_limited" if err.translation_key == "write_rate_limited"
                else "recovery_failed"
            )
        except (AmtronBusError, HomeAssistantError):
            return self._show_error("recovery_failed")
        if error != 0:
            return self._show_error("still_active")
        runtime.repair_state[ISSUE_EMS_HEARTBEAT_LOST] = None
        return self.async_create_entry(data={})

    def _show_error(self, error: str) -> RepairsFlowResult:
        return self.async_show_form(
            step_id="confirm", data_schema=None, errors={"base": error}
        )


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
    # The coordinator fires every few seconds. Writing an unchanged issue to
    # the issue registry each time is thousands of pointless writes a day.
    state = dict(placeholders or {}) if active else None
    remembered = entry.runtime_data.repair_state
    if issue in remembered and remembered[issue] == state:
        return
    remembered[issue] = state

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
