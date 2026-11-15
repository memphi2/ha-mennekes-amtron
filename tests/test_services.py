from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)

from custom_components.mennekes_amtron_rtu import services
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.command_events import command_source
from custom_components.mennekes_amtron_rtu.device.const import (
    ATTR_ALLOW_UNLIMITED,
    ATTR_CURRENT,
    DOMAIN,
    SERVICE_SET_CHARGING_CURRENT,
)
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.departure_plan import DepartureInputs
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from tests.ha_fakes import build_runtime


class FakeDeviceRegistry:
    def __init__(self, devices: dict[str, object]) -> None:
        self._devices = devices

    def async_get(self, device_id: str) -> object:
        return self._devices.get(device_id)


def _patch_registry(monkeypatch: pytest.MonkeyPatch, devices: dict[str, object]) -> None:
    monkeypatch.setattr(
        services.dr, "async_get", lambda _hass: FakeDeviceRegistry(devices)
    )


def test_the_action_is_registered_once() -> None:
    async def run() -> None:
        hass, _entry, _transport = await build_runtime()
        services.async_setup_services(hass)
        services.async_setup_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_SET_CHARGING_CURRENT)
        assert set(services.DEVICE_ACTIONS) == {
            "start_guest_charging", "use_calendar_departure", "use_vehicle_departure",
            "assign_vehicle_charge",
        }
        assert {service for domain, service in hass.services.registered if domain == DOMAIN} >= {
            SERVICE_SET_CHARGING_CURRENT, "get_recorder_exclusions", *services.DEVICE_ACTIONS,
        }

    asyncio.run(run())


def test_the_schema_rejects_the_invalid_band_through_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entry_id=entry.entry_id)}
        )
        services.async_setup_services(hass)
        handler, _schema = hass.services.registered[
            (DOMAIN, SERVICE_SET_CHARGING_CURRENT)
        ]

        await handler(
            SimpleNamespace(
                context=Context(),
                data={
                    "device_id": ["dev"],
                    ATTR_CURRENT: 10.0,
                    ATTR_ALLOW_UNLIMITED: False,
                }
            )
        )
        assert any(
            address == R.CHARGING_CURRENT_EMS.address
            for address, _ in transport.writes
        )

        with pytest.raises(ServiceValidationError):
            await handler(
                SimpleNamespace(
                    context=Context(),
                    data={
                        "device_id": ["dev"],
                        ATTR_CURRENT: 0.0,
                        ATTR_ALLOW_UNLIMITED: False,
                    }
                )
            )

    asyncio.run(run())


def test_the_schema_accepts_the_documented_fields() -> None:
    validated = services.SET_CHARGING_CURRENT_SCHEMA(
        {"device_id": "dev", ATTR_CURRENT: 6}
    )
    assert validated[ATTR_CURRENT] == 6.0
    assert validated[ATTR_ALLOW_UNLIMITED] is False


def test_an_unknown_device_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass, _entry, _transport = await build_runtime()
        _patch_registry(monkeypatch, {})
        with pytest.raises(ServiceValidationError) as excinfo:
            services.async_resolve_controls(hass, ["missing"])
        assert excinfo.value.translation_key == "unknown_device"

    asyncio.run(run())


def test_an_unloaded_entry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.runtime_data = None
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entry_id=entry.entry_id)}
        )
        with pytest.raises(ServiceValidationError) as excinfo:
            services.async_resolve_controls(hass, ["dev"])
        assert excinfo.value.translation_key == "entry_not_loaded"

    asyncio.run(run())


@pytest.mark.parametrize("domain", ["other", "mennekes_amtron"])
def test_a_device_of_another_integration_is_refused(
    monkeypatch: pytest.MonkeyPatch, domain: str,
) -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.domain = domain
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entry_id=entry.entry_id)}
        )
        with pytest.raises(ServiceValidationError) as excinfo:
            services.async_resolve_controls(hass, ["dev"])
        assert excinfo.value.translation_key == "no_wallbox_target"

    asyncio.run(run())


def test_an_unknown_entry_id_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass, _entry, _transport = await build_runtime()
        _patch_registry(
            monkeypatch, {"dev": SimpleNamespace(config_entry_id="nope")}
        )
        with pytest.raises(ServiceValidationError):
            services.async_resolve_controls(hass, ["dev"])

    asyncio.run(run())


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        async def call() -> None:
            self.calls.append(name)

        return call


@pytest.mark.parametrize(("action", "owner", "method", "missing"), [
    ("use_calendar_departure", "timed_goal", "async_use_next_calendar_event", "timed_goal_unavailable"),
    ("use_vehicle_departure", "timed_goal", "async_use_vehicle_departure", "timed_goal_unavailable"),
    ("assign_vehicle_charge", "charge_loss", "async_assign", "loss_unavailable"),
])
def test_each_device_action_reaches_its_owner_or_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, action: str, owner: str, method: str, missing: str,
) -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        services.async_setup_services(hass)
        handler, schema = hass.services.registered[(DOMAIN, action)]
        call = SimpleNamespace(context=Context(), data=schema({}))
        with pytest.raises(ServiceValidationError) as excinfo:
            await handler(call)
        assert excinfo.value.translation_key == missing
        recorder = _Recorder()
        setattr(entry.runtime_data, owner, recorder)
        await handler(call)
        assert recorder.calls == [method]
        assert transport.writes == []

    asyncio.run(run())


def test_without_any_loaded_wallbox_a_target_is_required() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.runtime_data = None
        with pytest.raises(ServiceValidationError) as excinfo:
            services._resolve_runtimes(hass, None)
        assert excinfo.value.translation_key == "wallbox_target_required"

    asyncio.run(run())


class _Permissions:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.checked: list[tuple[str, str]] = []

    def check_entity(self, entity_id: str, policy: str) -> bool:
        self.checked.append((entity_id, policy))
        return self.allowed


def _user(*, admin: bool = False, allowed: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id="user-1", is_admin=admin, permissions=_Permissions(allowed))


class _EntityRegistry:
    def __init__(self, entity_id: str | None) -> None:
        self.entity_id = entity_id

    def async_get_entity_id(self, domain: str, platform: str, unique_id: str) -> str | None:
        assert (domain, platform, unique_id) == ("number", DOMAIN, "ABC123456789_charging_current_limit")
        return self.entity_id


@pytest.mark.parametrize("service", [SERVICE_SET_CHARGING_CURRENT, "start_guest_charging"])
@pytest.mark.parametrize(("user", "entity_id", "outcome"), [
    (None, None, UnknownUser),
    (_user(admin=True), None, None),
    (_user(allowed=True), "number.amtron_limit", None),
    (_user(allowed=False), "number.amtron_limit", Unauthorized),
    (_user(allowed=True), None, Unauthorized),
])
def test_a_user_needs_control_of_the_current_limit_to_act(
    monkeypatch: pytest.MonkeyPatch, service, user, entity_id, outcome,
) -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        _patch_registry(monkeypatch, {"dev": SimpleNamespace(config_entry_id=entry.entry_id)})
        monkeypatch.setattr(services.er, "async_get", lambda _hass: _EntityRegistry(entity_id))

        async def get_user(_user_id: str):
            return user

        hass.auth = SimpleNamespace(async_get_user=get_user)
        started: list[str] = []

        async def start() -> None:
            started.append("guest")

        entry.runtime_data.guest = SimpleNamespace(async_start_guest=start)
        services.async_setup_services(hass)
        handler, schema = hass.services.registered[(DOMAIN, service)]
        fields = {ATTR_CURRENT: 10.0} if service == SERVICE_SET_CHARGING_CURRENT else {}
        call = SimpleNamespace(context=Context(user_id="user-1"), data=schema({"device_id": "dev", **fields}))
        if outcome is None:
            await handler(call)
            done = bool(transport.writes) if service == SERVICE_SET_CHARGING_CURRENT else started == ["guest"]
            assert done
        else:
            with pytest.raises(outcome):
                await handler(call)
            # Every target is authorized before the first one acts.
            assert transport.writes == []
            assert started == []

    asyncio.run(run())


# What Home Assistant asks of the service: every command reaches the real
# service, which owns the wallbox and the goals.


def test_the_control_reads_the_service_state_it_was_given() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        control = entry.runtime_data.control
        assert control.state_change_interval_seconds == 300
        assert control.phase_switch_grace_seconds == 5
        assert control.energy_goal_stop_requested is False
        assert control.charging_release_requested is None
        assert control.observed_pause is False
        data = entry.runtime_data.coordinator.data
        data.failed_blocks = ("current_limits",)
        assert control.observed_pause is None
        data.failed_blocks = ()
        data.values["charging_current_ems"] = 1.0
        assert control.observed_pause is True
        data.values["charging_current_ems"] = float("nan")
        assert control.observed_pause is None
        assert control.departure is entry.runtime_data.client.departure
        assert control.soc_configuration_required is (
            entry.runtime_data.client.snapshot["control"].get("soc_maximum_required") is None
        )
        # Without a gate in the service state, Plug & Charge is off.
        entry.runtime_data.client.snapshot["control"].pop("plug_charge", None)
        assert control.plug_charge == {
            "enabled": False, "policy": "", "session": None, "since": None,
            "authorized": False, "manual": False,
        }

    asyncio.run(run())


def test_a_lost_service_refuses_commands_before_sending() -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()
        control = entry.runtime_data.control
        entry.runtime_data.client.snapshot["available"] = False
        assert control.read_only
        with pytest.raises(HomeAssistantError) as excinfo:
            await control.async_set_lock_evse(True)
        assert excinfo.value.translation_key == "service_unavailable"
        assert transport.writes == []

    asyncio.run(run())


def test_resuming_honours_a_valid_cap_and_refuses_an_invalid_one() -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()
        control = entry.runtime_data.control
        await control.async_pause_charging()
        for cap in (float("nan"), 5.0):
            with pytest.raises(ServiceValidationError) as excinfo:
                await control.async_resume_charging(current_cap=cap)
            assert excinfo.value.translation_key == "charging_current_invalid"
        await control.async_resume_charging(current_cap=10.0)
        written = [
            decode_register(R.CHARGING_CURRENT_EMS, words)
            for address, words in transport.writes if address == R.CHARGING_CURRENT_EMS.address
        ]
        assert written == [1.0]
        # The service holds the resume for its state-change interval.
        assert control.pending_action == "resume"

    asyncio.run(run())


def test_cancelling_without_a_queued_change_publishes_nothing() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        published: list[int] = []
        entry.runtime_data.coordinator.async_add_listener(lambda: published.append(1))
        await entry.runtime_data.control.async_cancel_pending_change()
        assert published == []
        assert entry.runtime_data.control.pending_action is None

    asyncio.run(run())


def test_plug_and_charge_is_configured_and_switched_off_through_the_service() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        control = entry.runtime_data.control
        published: list[int] = []
        entry.runtime_data.coordinator.async_add_listener(lambda: published.append(1))
        await control.async_configure_plug_charge(True, policy="a" * 64)
        assert control.plug_charge["enabled"] is True
        # Only the plug-in the service reported can be authorized.
        with pytest.raises(ServiceValidationError) as excinfo:
            await control.async_authorize_plug_charge("other", deadline=datetime.now(UTC) + timedelta(minutes=5))
        assert excinfo.value.translation_key == "plug_charge_session_changed"
        await control.async_configure_plug_charge(False)
        assert control.plug_charge["enabled"] is False
        assert published

    asyncio.run(run())


def test_departure_choices_reach_the_service_plan() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        control = entry.runtime_data.control
        deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=8)
        await control.async_set_target_time(deadline)
        assert control.departure.trip.deadline == deadline
        await control.async_set_target_time(None)
        assert control.departure.trip is None or control.departure.trip.deadline is None
        await control.async_set_departure_inputs(DepartureInputs(vehicle="car", capacity_kwh=60.0))
        await control.async_set_departure_trip(deadline, "calendar.trips")
        assert control.departure.trip.deadline == deadline
        # A trip for another selection than the service holds is refused.
        with pytest.raises(HomeAssistantError) as excinfo:
            await control.async_set_departure_trip(None, None, withdraw=True, expected_selection={})
        assert excinfo.value.translation_key == "soc_goal_source_changed"
        await control.async_set_departure_trip(None, None, withdraw=True)

    asyncio.run(run())


def test_the_charge_level_goal_is_reserved_and_committed_by_the_service() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        control = entry.runtime_data.control
        await control.async_configure_soc("sensor.car_soc")
        # The same configuration again sends nothing.
        await control.async_configure_soc("sensor.car_soc")
        await control.async_observe_soc(50.0, source="sensor.car_soc")
        await control.async_set_soc_goal(80.0, source="sensor.car_soc", soc=50.0)
        assert control.soc_goal.target_percent == 80.0
        assert not control.soc_goal_resolution_pending
        assert await control.async_resolve_soc_goal() == "superseded"
        revision = control.soc_goal_revision
        assert isinstance(revision, str)
        await control.async_observe_soc(55.0, source="sensor.car_soc", expected_soc_revision=revision)
        with pytest.raises(ServiceValidationError) as excinfo:
            await control.async_set_soc_goal(150.0, source="sensor.car_soc")
        assert excinfo.value.translation_key == "soc_goal_invalid"

        entry.runtime_data.client.snapshot["control"]["supports_soc_goal"] = False
        for attempt in (
            control.async_set_soc_goal(80.0, source="sensor.car_soc"),
            control.async_observe_soc(50.0, source="sensor.car_soc"),
        ):
            with pytest.raises(ServiceValidationError) as excinfo:
                await attempt
            assert excinfo.value.translation_key == "soc_goal_not_supported"

    asyncio.run(run())


@pytest.mark.parametrize(("reply", "error"), [
    ({"token": "not-a-token"}, "service_unavailable"),
    (AmtronConnectionError("offline"), "service_unavailable"),
    ({"outcome": "rejected"}, "soc_goal_unavailable"),
    ({"outcome": "applied", "revision": "other"}, "soc_goal_unavailable"),
    ({"outcome": "bogus"}, "service_unavailable"),
])
def test_an_unusable_goal_transaction_is_reported_not_guessed(reply, error) -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        control = entry.runtime_data.control
        client = entry.runtime_data.client
        await control.async_configure_soc("sensor.car_soc")
        snapshot = client.snapshot
        token = "0" * 32

        async def request(_method, path, _payload=None, **_kwargs):
            if isinstance(reply, Exception):
                raise reply
            if path.endswith("/reserve") and "token" in reply:
                return reply
            if path.endswith("/reserve"):
                return {"token": token}
            return {**reply, "snapshot": snapshot}

        client.request = request
        with pytest.raises(HomeAssistantError) as excinfo:
            await control.async_set_soc_goal(80.0, source="sensor.car_soc", soc=50.0)
        assert excinfo.value.translation_key == error

    asyncio.run(run())


def test_an_unknown_goal_outcome_fences_until_the_service_resolves_it() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        control = entry.runtime_data.control
        client = entry.runtime_data.client
        await control.async_configure_soc("sensor.car_soc")
        snapshot = client.snapshot
        outcomes = ["unknown", "unknown", "unknown", "applied"]

        async def request(_method, path, _payload=None, **_kwargs):
            if path.endswith("/reserve"):
                return {"token": "1" * 32}
            return {"outcome": outcomes.pop(0), "snapshot": snapshot, "revision": control.soc_goal_revision}

        client.request = request
        with pytest.raises(HomeAssistantError) as excinfo:
            await control.async_set_soc_goal(80.0, source="sensor.car_soc", soc=50.0)
        assert excinfo.value.translation_key == "service_unavailable"
        assert control.soc_goal_resolution_pending
        assert not control.soc_goal_resolution_fenced
        with pytest.raises(DeviceError):
            control.abandon_fenced_soc_goal()
        # A second target waits until the first one is known.
        with pytest.raises(HomeAssistantError):
            await control.async_set_soc_goal(70.0, source="sensor.car_soc", soc=50.0)
        assert await control.async_resolve_soc_goal() == "unknown"
        assert control.soc_goal_resolution_fenced
        control.abandon_fenced_soc_goal()
        assert not control.soc_goal_resolution_pending

        control._soc_transaction = ("1" * 32, control.soc_goal_revision)
        assert await control.async_resolve_soc_goal() == "unknown"
        assert await control.async_resolve_soc_goal() == "applied"
        assert not control.soc_goal_resolution_pending

    asyncio.run(run())


@pytest.mark.parametrize(("caller", "recorded"), [("unknown", "home_assistant"), ("app", "app")])
def test_a_command_keeps_the_source_that_asked_for_it(caller, recorded) -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        sources: list[str] = []
        entry.service.control.events.listen(lambda event: sources.append(event.source))
        token = command_source.set(caller)
        try:
            await entry.runtime_data.control.async_set_lock_evse(True)
            # The caller's own source is restored afterwards.
            assert command_source.get() == caller
        finally:
            command_source.reset(token)
        assert sources == [recorded]

    asyncio.run(run())
