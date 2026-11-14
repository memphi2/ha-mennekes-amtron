"""Optional vehicle-based grants; the bus owner enforces the physical gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .device.client_errors import AmtronBusError
from .device.const import DOMAIN
from .device.errors import DeviceError
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error
from .plug_charge_explanation import explain_detection
from .vehicle_detection import SETTLE_SECONDS, vehicle_evidence
from .vehicle_profiles import (
    CONF_VIRTUAL_PLUG_CHARGE,
    active_profile,
    profiles,
    without_selection,
)
from .vehicle_sources import PLUG_WINDOW

_LOGGER = logging.getLogger(__name__)

# An armed departure plan owns the current. The grant waits until the plan has
# applied its decision, so a release never starts a charge it must then stop.
_PLAN_DECIDING = frozenset({"waiting_for_device", "queued", "write_pending"})
_PLAN_CHARGING = frozenset({"charging", "finishing", "unreachable", "late"})
_PLAN_PAUSED = "paused_until_start"


def plug_charge_policy(options: Mapping[str, Any]) -> str:
    """Revoke grants when identity or evidence change, not on reload.

    A profile with detection signals takes part; the wallbox switch is the only
    permission.
    """
    policy = {
        key: {
            "provider": profile.provider_id,
            "device": profile.device_id,
            "identity": profile.identity,
            "detection": profile.detection.as_dict() if profile.detection else None,
        }
        for key, profile in profiles(options).items()
    }
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


class PlugChargeObserver:
    """Grant a bounded physical session after fresh, unique vehicle evidence."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._control = entry.runtime_data.control
        self._coordinator = entry.runtime_data.coordinator
        self._evidence = vehicle_evidence(hass, entry)
        self._policy = plug_charge_policy(entry.options)
        self._configured_options = deepcopy(dict(entry.options))
        self._switch_applied = False
        self._pending_enabled: bool | None = None
        self._session: str | None = None
        # Only a plug-in seen while running is new. After a restart, reconnect
        # or app update the same car gets a new session but keeps its state.
        self._unplugged = False
        self._fresh_session: str | None = None
        self._candidate: tuple[str, str] | None = None
        self._timer: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[None] | None = None
        self._target_task: asyncio.Task[None] | None = None
        self._target_session: str | None = None
        self._target_watch: tuple[str, object, int] | None = None
        self._target_retry_at = 0.0
        self._unsubs: list[CALLBACK_TYPE] = []
        self._evidence_unsubscribe: CALLBACK_TYPE | None = None
        self._stopped = False
        self._retry_at = 0.0
        self._generation = 0
        self._status = "waiting_vehicle"
        self._blocked_reason: str | None = None
        self._decision_reason = "waiting_vehicle"
        self._last_grant_error: str | None = None

    @property
    def status(self) -> str:
        """A stable enum, without session tokens or ticking age attributes."""
        return self._status

    @property
    def blocked_reason(self) -> str | None:
        """The first failed release condition while the status is blocked."""
        return self._blocked_reason

    @property
    def decision_reason(self) -> str:
        """Explain waiting as well as blocking, without changing state enums."""
        return self._decision_reason

    @staticmethod
    def _gate_since(raw: object) -> datetime | None:
        """When the gate saw the current plug-in; None unless timezone-aware."""
        since = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
        return since if since is not None and since.tzinfo is not None else None

    def _detection_details(self, since: datetime | None) -> dict[str, Any]:
        active = active_profile(self._entry.options)
        return explain_detection([
            (active is not None and active.id == key, self._evidence.snapshot(key))
            for key, profile in profiles(self._entry.options).items()
            if profile.detection is not None
        ], since)

    @property
    def decision_details(self) -> dict[str, Any]:
        """Read-only support snapshot; no VIN, location, token or raw exception."""
        since = self._gate_since(self._control.plug_charge.get("since"))
        values = self._coordinator.current_data().values
        return {
            "reason": self._decision_reason,
            "last_grant_error": self._last_grant_error,
            "release_blockers": list(self._release_blockers()),
            "unavailable_registers": [
                key for key in ("lock_evse", "error_code", "authorization_enabled")
                if values.get(key) is None
            ],
            "departure_plan": self._plan_status(),
            "detection": self._detection_details(since),
        }

    @callback
    def start(self) -> None:
        self._unsubs = [
            self._coordinator.async_add_listener(self._changed),
        ]
        self._changed()

    def options_applied(self) -> bool:
        """Skip only the option notification for an already applied switch."""
        return (
            self._switch_applied
            # A vehicle switch is applied at runtime and must not count as
            # an unrelated settings change here.
            and without_selection(self._configured_options) == without_selection(self._entry.options)
            and self._entry.runtime_data.entry_data == dict(self._entry.data)
        )

    async def async_set_enabled(self, enabled: bool) -> None:
        """Apply and remember the gate without restarting its connection owner."""
        locks: dict[str, asyncio.Lock] = self._hass.data.setdefault(f"{DOMAIN}_reload_locks", {})
        async with locks.setdefault(self._entry.entry_id, asyncio.Lock()):
            if self._stopped or self._entry.runtime_data.plug_charge is not self:
                raise service_validation_error("plug_charge_unavailable")
            if not self._evidence.current() or plug_charge_policy(self._entry.options) != self._policy:
                raise service_validation_error("plug_charge_session_changed")
            self._pending_enabled = None
            try:
                await self._control.async_configure_plug_charge(enabled, policy=self._policy)
            finally:
                # A deny write may fail after the owner has applied the policy.
                # A lost HTTP reply may reveal that change only on a later poll.
                # Preserve the original error, but follow confirmed owner state.
                self._pending_enabled = enabled
                self._changed()
                self._coordinator.async_update_listeners()

    def _commit_switch_if_applied(self) -> None:
        enabled = self._pending_enabled
        gate = self._control.plug_charge
        if self._stopped or enabled is None or gate.get("enabled") is not enabled or gate.get("policy") != self._policy:
            return
        self._pending_enabled = None
        # Merge into current options so an unrelated save during the command is
        # preserved. Only our bound snapshot may skip the resulting reload.
        self._configured_options[CONF_VIRTUAL_PLUG_CHARGE] = enabled
        self._switch_applied = True
        self._hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_VIRTUAL_PLUG_CHARGE: enabled},
        )

    def _selection(self) -> tuple[str, str] | None:
        gate = self._control.plug_charge
        if (
            self._stopped or not self._evidence.current()
            or self._entry.options.get(CONF_VIRTUAL_PLUG_CHARGE) is not True
            or gate.get("policy") != self._policy
        ):
            self._set_status("disabled")
            return None
        if not self._coordinator.last_update_success:
            self._set_status("blocked", "connection_lost")
            return None
        if not gate.get("enabled"):
            self._set_status("blocked", "gate_inactive")
            return None
        session = gate.get("session")
        session = session if isinstance(session, str) else None
        if session != self._session:
            if session is not None and self._unplugged:
                self._fresh_session = session
            self._session = session
            self._retry_at = 0.0
            self._last_grant_error = None
        self._unplugged = session is None
        if gate.get("manual") is True and (
            self._coordinator.current_data().values.get("charging_release") == 1
            or self._control.pending_action == "enable_charging"
        ):
            # Released by hand for this or the next connection, any car. A
            # manual release whose command was replaced never opened the
            # gate; the car's own grant then still applies.
            self._set_status("manual_release")
            return None
        if not isinstance(session, str):
            self._set_status("waiting_vehicle")
            return None
        # Once accepted, stale cloud data cannot interrupt this physical cycle.
        # A policy change or lost physical connection is revoked by the owner.
        if gate.get("authorized"):
            if self._target_watch is not None and self._target_watch[0] == session:
                self._adopt_vehicle_target(session)
            self._last_grant_error = None
            self._set_status("authorized")
            return None
        since = self._gate_since(gate.get("since"))
        selected = self._evidence.selected(since)
        if selected is None:
            self._set_status("waiting_detection", detail=self._detection_details(since)["reason"])
            return None
        profile = active_profile(self._entry.options)
        if profile is None or profile.id != selected:
            # Another car, or none, is selected manually.
            self._set_status("vehicle_not_selected")
            return None
        binding = getattr(self._entry.runtime_data, "vehicle_binding", None)
        if self._entry.runtime_data.vehicle_profile_id != profile.id or (
            binding is not None and binding.resting
        ):
            # The car's target and departure plan are still being started,
            # or wait for the car to confirm this plug-in.
            # Grant nothing before they apply; the switch ends with an update.
            self._set_status("waiting_detection", detail="vehicle_binding_pending")
            return None
        evidence = self._evidence.snapshot(selected)
        if (
            evidence.connected_at is None or evidence.connected_at.tzinfo is None
            or since is None or evidence.connected_at < since - PLUG_WINDOW
        ):
            detail = "session_time_unknown" if since is None else "plug_report_not_current"
            self._set_status("waiting_detection", detail=detail)
            return None
        self._adopt_vehicle_target(session)
        reason = self._release_blocker()
        if reason is not None:
            self._set_status("blocked", reason)
            return None
        self._set_status("waiting_release", detail="grant_retry" if self._last_grant_error else "settling")
        return selected, session

    def _release_blocker(self) -> str | None:
        """Keep the existing first-blocker priority for automations."""
        reasons = self._release_blockers()
        return reasons[0] if reasons else None

    def _release_blockers(self) -> tuple[str, ...]:
        """List simultaneous blockers without changing any control intent."""
        values = self._coordinator.current_data().values
        checks = (
            ("read_only", self._control.read_only),
            ("departure_plan", not self._plan_applied()),
            ("pause_requested", self._control.pause_requested is True and not self._pause_allows_grant()),
            ("release_off", self._control.charging_release_requested is False),
            ("energy_goal_stop", bool(self._control.energy_goal_stop_requested)),
        )
        reasons = [reason for reason, failed in checks if failed]
        for key, reason in (
            ("lock_evse", "wallbox_locked"), ("error_code", "error_code"),
            ("authorization_enabled", "authorization_required"),
        ):
            value = values.get(key)
            if value is None:
                reasons.append("register_unavailable")
            elif value != 0:
                reasons.append(reason)
        return tuple(dict.fromkeys(reasons))

    def _plan_status(self) -> str | None:
        """The status of an armed departure plan that controls the current."""
        timed = self._entry.runtime_data.timed_goal
        if timed is None or timed.goal.deadline is None or timed.goal.finished is not None:
            return None
        status = timed.status
        return status if status in _PLAN_DECIDING | _PLAN_CHARGING | {_PLAN_PAUSED} else None

    def _plan_applied(self) -> bool:
        plan = self._plan_status()
        if plan is None:
            return True
        if plan == _PLAN_PAUSED:
            return self._control.pause_requested is True
        return plan in _PLAN_CHARGING and self._control.pause_requested is not True

    def _pause_allows_grant(self) -> bool:
        """A permitted plug-in ends a manual pause, never an automatic one.

        Goal stops are refused separately. An applied departure plan keeps its
        pause and resumes on schedule. A queued command other than a resume is
        newer intent.
        """
        return self._plan_status() is not None or self._control.pending_action in (None, "resume")

    def _adopt_vehicle_target(self, session: str) -> None:
        """Start each new plug-in at the car's own charge limit.

        An explicit new target also ends a reached latch left over from the
        previous connection. It never starts charging by itself.
        """
        if session != self._fresh_session or session == self._target_session:
            return
        observer = self._entry.runtime_data.vehicle_soc
        if observer is None:
            return
        revision = getattr(observer, "target_choice_generation", 0)
        if self._target_watch is None or self._target_watch[0] != session:
            self._target_watch = (session, observer, revision)
            self._target_retry_at = 0.0
        elif self._target_watch != (session, observer, revision):
            self._target_session = session
            return
        if self._control.read_only or not self._control.supports_soc_goal:
            return
        if (self._target_task is not None and not self._target_task.done()) or time.monotonic() < self._target_retry_at:
            return
        limit, goal = observer.maximum, self._control.soc_goal
        if limit is None:
            return
        if limit == goal.target_percent and goal.status != "reached":
            self._target_session = session
            return

        async def adopt() -> None:
            try:
                await observer.async_set_target(limit, automatic=True, expected_choice=revision)
            except (HomeAssistantError, DeviceError, OSError):
                self._target_retry_at = time.monotonic() + 5
                _LOGGER.debug("Vehicle charge limit adoption deferred")
            else:
                self._target_session = session

        self._target_task = self._hass.async_create_task(adopt())

    def _set_status(self, status: str, reason: str | None = None, *, detail: str | None = None) -> None:
        detail = detail or reason or status
        if (self._status, self._blocked_reason, self._decision_reason) != (status, reason, detail):
            self._status = status
            self._blocked_reason = reason
            self._decision_reason = detail
            # Defer listeners to avoid recursion through our own subscription.
            self._hass.loop.call_soon(self._notify)

    @callback
    def _notify(self) -> None:
        if not self._stopped:
            self._coordinator.async_update_listeners()

    @callback
    def _changed(self, _event: object = None) -> None:
        self._commit_switch_if_applied()
        enabled = not self._stopped and self._entry.options.get(CONF_VIRTUAL_PLUG_CHARGE) is True
        if enabled and self._evidence_unsubscribe is None:
            self._evidence_unsubscribe = self._evidence.subscribe(self._changed)
        elif not enabled and self._evidence_unsubscribe is not None:
            self._evidence_unsubscribe()
            self._evidence_unsubscribe = None
        selected = self._selection()
        if selected != self._candidate:
            self._cancel_timer()
            self._candidate = selected
            gate = self._control.plug_charge
            if (
                self._task is not None and not self._task.done()
                and not (gate.get("authorized") and gate.get("session") == self._session)
            ):
                self._task.cancel()
        if selected is None or self._timer is not None or (
            self._task is not None and not self._task.done()
        ) or time.monotonic() < self._retry_at:
            return
        generation = self._generation

        @callback
        def settled(now: object) -> None:
            if generation == self._generation:
                self._settled(now)

        self._timer = async_call_later(self._hass, SETTLE_SECONDS, settled)

    @callback
    def _settled(self, _now: object) -> None:
        self._timer = None
        candidate = self._candidate
        if candidate is not None and self._selection() == candidate:
            self._task = self._hass.async_create_task(self._authorize(candidate))

    async def _authorize(self, candidate: tuple[str, str]) -> None:
        try:
            if self._selection() == candidate:
                if (
                    candidate[1] == self._fresh_session
                    and self._control.pause_requested is True and self._control.pending_action is None
                    and self._plan_status() is None
                ):
                    # The gate keeps the release off until the grant below, so
                    # this resume switches no charge and starts no long guard.
                    # A restored session keeps the pause; the grant then only
                    # restores the release at the pause current.
                    await self._control.async_resume_charging()
                await self._control.async_authorize_plug_charge(
                    candidate[1], deadline=dt_util.utcnow() + timedelta(seconds=10),
                )
                self._selection()
        except (AmtronBusError, HomeAssistantError, DeviceError, OSError) as err:
            # The owner may still be in its configured switching interval.
            # Retry on a later ordinary poll/report, never spin or force polls.
            # Stable categories only: raw errors may include an adapter address.
            key = getattr(err, "translation_key", None)
            failure = key if key in {
                "write_rate_limited", "control_state_unknown", "plug_charge_blocked",
                "plug_charge_session_changed", "plug_charge_authorization_expired",
                "energy_goal_stop_active", "service_job_active", "read_only_mode",
            } else "command_failed"
            if not self._stopped and self._session == candidate[1]:
                self._last_grant_error = failure
                if self._status == "waiting_release":
                    self._set_status("waiting_release", detail="grant_retry")
            _LOGGER.debug("Virtual charging release deferred: %s", err)
        finally:
            self._retry_at = time.monotonic() + 5

    def _cancel_timer(self) -> None:
        self._generation += 1
        if self._timer is not None:
            self._timer()
            self._timer = None

    async def async_stop(self) -> None:
        """Detach observers; a confirmed app grant survives an HA restart."""
        self._stopped = True
        self._pending_enabled = None
        self._cancel_timer()
        if self._evidence_unsubscribe is not None:
            self._evidence_unsubscribe()
            self._evidence_unsubscribe = None
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()
        for task in (self._task, self._target_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
