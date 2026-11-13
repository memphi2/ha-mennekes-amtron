"""The MENNEKES AMTRON integration.

This module is a thin facade: it assembles the runtime data of one config
entry and tears it down again. Every rule about the device lives in the
focused modules next to it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, NoReturn

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed

from .blueprint_installer import async_install_blueprints
from .charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    LOSS_SOURCE_DISABLED,
    LOSS_SOURCE_SENSOR,
)
from .charge_loss_observer import ChargeLossObserver
from .charging_cost_const import (
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_COST_SOURCE,
    COST_SOURCE_DISABLED,
)
from .control import AmtronControl, ControlOptions
from .coordinator import MennekesAmtronCoordinator
from .cost_observer import ChargingCostObserver, LastChargingCost
from .data import (
    ConnectionState,
    MennekesAmtronRuntimeData,
    RuntimeHandoff,
    WallboxData,
    WriteDiagnostics,
)
from .device.client import MennekesModbusClient, SerialConfig
from .device.client_errors import AmtronBusError
from .device.connection import ConnectionConfig, NetworkConfig
from .device.const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONNECTION_TYPE,
    CONF_CURRENT_LIMIT,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PARITY,
    CONF_PHASE_SWITCH_GRACE_SECONDS,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_SERIAL_NUMBER,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
    CONF_STOPBITS,
    CONF_TCP_AUTH_PASSWORD,
    CONF_TCP_AUTHENTICATION,
    CONF_TCP_PORT,
    CONNECTION_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
    DEFAULT_NAME,
    DEFAULT_PARITY,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    DOMAIN,
)
from .device.energy_goal_controller import EnergyGoalController
from .device.errors import device_error
from .device.heartbeat import HeartbeatTask
from .device.identity import async_read_identity
from .device.soc_goal_controller import SocGoalController
from .device.waveshare_auth import WaveshareAuthenticationRejectedError
from .device.write_state import WriteState
from .entry_types import MennekesAmtronConfigEntry
from .goal_store import (
    GoalStore,
    ModelStore,
    SessionHistoryStore,
    SocGoalStore,
    TimedGoalStore,
)
from .loss_attribution import (
    forget_loss_attribution,
    loss_vehicle_options,
    track_loss_attribution,
)
from .loss_store import LossStore, async_prune_profile_losses
from .network_tariff_year import (
    async_refresh_tariff_year,
    async_track_tariff_midnight,
    needs_year_refresh,
)
from .options_flow import control_mode_is_master
from .plug_charge import PlugChargeObserver, plug_charge_policy
from .recovery import HeartbeatRecovery
from .remote import (
    CONF_SERVER_TOKEN,
    CONF_SERVER_URL,
    RemoteClient,
    RemoteControl,
    RemoteCoordinator,
    RemoteHeartbeat,
)
from .remote_errors import ServiceConnectionError
from .repairs import async_check_repairs, async_setup_repairs
from .services import async_setup_services
from .session_observer import SessionObserver, session_capture_enabled
from .timed_goal_const import (
    CONF_TIMED_GOAL_ENABLED,
    CONF_TIMED_GOAL_RESET_PENDING,
    timed_goal_is_enabled,
)
from .timed_goal_observer import TimedGoalObserver
from .vehicle_binding import VehicleBinding, VehicleBindingStore
from .vehicle_detection import (
    VehicleDetectionObserver,
    automatic_selection_available,
    prepare_vehicle_selection,
    vehicle_selection_matches,
)
from .vehicle_phases import VehiclePhaseStore
from .vehicle_profiles import (
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_VEHICLE_LIMIT_USE_PROFILE,
    CONF_VEHICLE_PROFILE_ID,
    CONF_VEHICLE_PROFILES,
    CONF_VEHICLE_SOC_USE_PROFILE,
    CONF_VIRTUAL_PLUG_CHARGE,
    SELECTION_KEYS,
    each_vehicle_options,
    profile_options,
    profiles,
    vehicle_options,
    vehicle_soc_is_configured,
    without_selection,
)
from .vehicle_soc import VehicleSocObserver
from .vehicle_soc_const import CONF_VEHICLE_CHARGE_LIMIT_ENTITY, CONF_VEHICLE_SOC_ENTITY
from .vehicle_sources import resolve_telemetry_source
from .vehicle_targets import VehicleTargetMemory, VehicleTargetStore
from .vehicle_trips import VehicleTripMemory, VehicleTripStore

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_LOGGER = logging.getLogger(__name__)

READ_PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT)
CONTROL_PLATFORMS = (
    Platform.NUMBER,
    Platform.DATETIME,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BUTTON,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""

    async_setup_services(hass)
    await async_install_blueprints(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> bool:
    """Set up one wallbox from a config entry."""

    # The preceding runtime is already unloaded. Clear a deliberately disabled
    # trip before connecting, including when the service is currently offline.
    # A failed optional cleanup leaves wallbox control available but cannot
    # restore the old trip, even if the feature has since been re-enabled.
    prepare_vehicle_selection(hass, entry)
    timed_reset_complete = await _async_reset_disabled_trip(hass, entry)
    options = vehicle_options(entry.options)

    def validate_vehicle_selection() -> None:
        if not vehicle_selection_matches(hass, entry, options.get(CONF_VEHICLE_PROFILE_ID)):
            prepare_vehicle_selection(hass, entry)
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="vehicle_selection_changed",
            )

    if CONF_VEHICLE_PROFILES in entry.options:
        catalog = profiles(entry.options)
        raw_catalog = entry.options.get(CONF_VEHICLE_PROFILES)
        if isinstance(raw_catalog, dict) and len(catalog) == len(raw_catalog):
            try:
                await async_prune_profile_losses(hass, entry.entry_id, catalog)
            except OSError:
                _LOGGER.warning("Deleted vehicle loss storage could not be removed; cleanup will retry on setup")
    remote = CONF_SERVER_URL in entry.data
    expected_serial = _entry_serial_binding(entry)
    handoff: RuntimeHandoff | None = hass.data.get(f"{DOMAIN}_reload_clients", {}).get(entry.entry_id)
    if handoff is not None and handoff.client is not None and (
        remote or handoff.client.config != _connection_config(entry)
    ):
        await handoff.async_close()
    client: MennekesModbusClient
    if remote:
        # Direct-mode intent must not return after a later switch back from
        # the service, which has since owned the authoritative charging state.
        hass.data.get(f"{DOMAIN}_write_states", {}).pop(entry.entry_id, None)
        client = RemoteClient(
            async_get_clientsession(hass),
            str(entry.data[CONF_SERVER_URL]),
            str(entry.data[CONF_SERVER_TOKEN]),
        )
        client.bind_identity(expected_serial)
    else:
        states: dict[str, WriteState] = hass.data.setdefault(
            f"{DOMAIN}_write_states", {}
        )
        if entry.entry_id not in states:
            states[entry.entry_id] = WriteState.cold_start(time.monotonic())
        retained_client = handoff.client if handoff is not None else None
        client = retained_client or MennekesModbusClient(
            _connection_config(entry), write_state=states[entry.entry_id]
        )
    try:
        await client.async_connect()
        identity = (
            client.identity
            if isinstance(client, RemoteClient)
            else await async_read_identity(client)
        )
        if expected_serial is not None and identity.serial_number != expected_serial:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="device_identity_changed"
            )
        if isinstance(client, RemoteClient):
            # Legacy endpoint-only entries keep their registry identifiers, but
            # can still bind this running client to a serial it has just read.
            client.bind_identity(identity.serial_number)
        validate_vehicle_selection()
    except BaseException as err:
        await client.async_close()
        if isinstance(err, WaveshareAuthenticationRejectedError):
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="gateway_authentication_failed"
            ) from err
        if isinstance(err, ServiceConnectionError) and err.reason == "service_auth_failed":
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="service_auth_failed"
            ) from err
        if isinstance(err, ServiceConnectionError) and err.reason == "service_identity_changed":
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="device_identity_changed"
            ) from err
        if isinstance(err, ServiceConnectionError) and err.reason == "service_version_mismatch":
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="service_version_mismatch"
            ) from err
        if isinstance(err, AmtronBusError):
            raise ConfigEntryNotReady(str(err)) from err
        raise

    # Update only the former generated title. HA preserves device names chosen
    # by the user separately, and existing registry entity IDs do not change.
    if entry.title == "MENNEKES AMTRON":
        hass.config_entries.async_update_entry(entry, title=DEFAULT_NAME)

    diagnostics = WriteDiagnostics()
    connection_state = ConnectionState()
    coordinator_type = RemoteCoordinator if remote else MennekesAmtronCoordinator
    coordinator = coordinator_type(
        hass,
        entry,
        client=client,
        identity=identity,
        connection_state=connection_state,
        timeline=diagnostics.timeline,
        scan_interval=_scan_interval(entry),
        measurement_interval=float(
            entry.options.get(
                CONF_MEASUREMENT_INTERVAL_SECONDS, DEFAULT_MEASUREMENT_INTERVAL_SECONDS
            )
        ),
    )

    def notify_pending() -> None:
        # Cancelling queued work or changing a goal is not a successful poll.
        # Notify entities without clearing a connection failure or reading the bus.
        if coordinator.data is not None:
            coordinator.data.pending_change = control.pending_action
            coordinator.data.pending_change_until = control.pending_until
        coordinator.async_update_listeners()

    if isinstance(client, RemoteClient):
        control: AmtronControl = RemoteControl(
            client,
            diagnostics=diagnostics,
            options=_control_options(entry),
            state=coordinator.current_data,
            request_refresh=coordinator.async_refresh,
            notify_pending=notify_pending,
        )
    else:
        def validate_deferred() -> None:
            if not coordinator.last_update_success:
                raise device_error("control_state_unknown")

        control = AmtronControl(
            client,
            diagnostics=diagnostics,
            identity=identity,
            options=_control_options(entry),
            state=coordinator.current_data,
            request_refresh=coordinator.async_request_refresh,
            notify_write_error=lambda: async_check_repairs(hass, entry),
            validate_deferred=validate_deferred,
            notify_pending=notify_pending,
        )
        coordinator.control_status = lambda: (control.pending_action, control.pending_until)
        coordinator.observe_recovery = control.observe_recovery
        coordinator.observe_plug_charge = lambda data, observed_at: control.observe_plug_charge(
            data, observed_at=observed_at,
        )
    entry.runtime_data = MennekesAmtronRuntimeData(
        client=client,
        coordinator=coordinator,
        control=control,
        identity=identity,
        diagnostics=diagnostics,
        connection_state=connection_state,
        entry_data=dict(entry.data),
    )
    try:
        if handoff is not None:
            # The new runtime now owns the connection, including failed-setup
            # cleanup. Transient estimates must never cross a changed wallbox.
            handoff.client = None
            if handoff.identity != identity or (
                not remote and handoff.connection_generation != client.connection_generation
            ):
                await handoff.async_close()
                forget_loss_attribution(hass, entry.entry_id)
        if profiles(entry.options):
            entry.runtime_data.loss_attribution_unsubscribe = track_loss_attribution(hass, entry)
        validate_vehicle_selection()
        # Configure the gate before starting heartbeat/recovery. The app owns
        # the previous grant; an unchanged policy survives this HA reload.
        plug_enabled = entry.options.get(CONF_VIRTUAL_PLUG_CHARGE) is True
        if plug_enabled or (
            CONF_VIRTUAL_PLUG_CHARGE in entry.options and control.supports_plug_charge
            and control.plug_charge.get("enabled") is True
        ):
            try:
                await control.async_configure_plug_charge(
                    plug_enabled, policy=plug_charge_policy(entry.options),
                )
            except (AmtronBusError, HomeAssistantError) as err:
                _raise_optional_setup_error(client, err, "plug_charge_unavailable")
        if control.supports_plug_charge:
            entry.runtime_data.plug_charge = PlugChargeObserver(hass, entry)
        await _async_resume_trip(hass, entry, timed_reset_complete)
        await _async_cancel_old_trip(hass, entry, timed_reset_complete)
        cost_enabled = entry.options.get(CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED) != COST_SOURCE_DISABLED
        if session_capture_enabled(entry):
            if not remote:
                retained_sessions = handoff.sessions if handoff is not None else None
                entry.runtime_data.sessions = retained_sessions or SessionObserver(hass, entry, identity, coordinator)
                if handoff is not None and retained_sessions is not None:
                    retained_sessions.rebind(coordinator)
                    handoff.sessions = None
                await entry.runtime_data.sessions.async_setup()
            if cost_enabled:
                retained_cost = handoff.charging_cost if handoff is not None else None
                if handoff is not None and retained_cost is not None and retained_cost.retain_for_reload(entry, remote=remote):
                    retained_cost.rebind(entry)
                    entry.runtime_data.charging_cost = retained_cost
                    handoff.charging_cost = None
                else:
                    if handoff is not None and retained_cost is not None:
                        await retained_cost.async_stop()
                        handoff.charging_cost = None
                    entry.runtime_data.charging_cost = ChargingCostObserver(hass, entry, remote=remote)
                    await entry.runtime_data.charging_cost.async_start()

            estimates_generation = client.connection_generation if not remote else None

            async def observe_estimates(data: WallboxData | None) -> None:
                nonlocal estimates_generation
                if not remote and estimates_generation != client.connection_generation:
                    estimates_generation = client.connection_generation
                    if entry.runtime_data.sessions is not None:
                        await entry.runtime_data.sessions.async_observe(None)
                    if entry.runtime_data.charging_cost is not None:
                        await entry.runtime_data.charging_cost.async_observe(None)
                if entry.runtime_data.sessions is not None:
                    await entry.runtime_data.sessions.async_observe(data)
                if entry.runtime_data.charging_cost is not None:
                    await entry.runtime_data.charging_cost.async_observe(data)

            coordinator.observe_session = observe_estimates
        if not remote:
            soc_store = SocGoalStore(
                hass, entry.entry_id,
                identity.serial_number or f"{client.config.port}:{client.config.device_id}",
            )
            try:
                client.write_state.soc_goal = await soc_store.async_load()
            except (OSError, ValueError) as err:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN, translation_key="soc_goal_state_invalid"
                ) from err

            async def persist_soc_goal() -> None:
                await soc_store.async_save(control.soc_goal)

            entry.runtime_data.soc_goal = SocGoalController(control, persist_soc_goal, notify_pending)
        if not remote and control.supports_energy_goal:
            store = GoalStore(
                hass, entry.entry_id,
                identity.serial_number or f"{client.config.port}:{client.config.device_id}",
            )
            try:
                client.write_state.energy_goal = await store.async_load()
            except (OSError, ValueError) as err:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN, translation_key="energy_goal_state_invalid"
                ) from err

            async def persist_goal() -> None:
                await store.async_save(control.energy_goal)

            goal = EnergyGoalController(control, client, persist_goal, notify_pending)
            entry.runtime_data.goal = goal
            coordinator.observe_goal = goal.observe
        if isinstance(client, RemoteClient):
            client.on_auth_failed = lambda: entry.async_start_reauth(hass)
            heartbeat = RemoteHeartbeat(client, diagnostics)
            entry.runtime_data.heartbeat = heartbeat

            def update_remote() -> None:
                heartbeat.update()
                if not client.connected:
                    # A command or diagnostics request can fail long before
                    # the next scheduled poll. Reflect this in entity state now.
                    key = {
                        "service_auth_failed": "service_auth_failed",
                        "service_identity_changed": "device_identity_changed",
                        "service_version_mismatch": "service_version_mismatch",
                    }.get(client.failure_reason or "", "service_unavailable")
                    coordinator.async_set_update_error(UpdateFailed(
                        translation_domain=DOMAIN, translation_key=key
                    ))
                counters = client.snapshot.get("diagnostics", {})
                for name in (
                    "heartbeats_sent",
                    "heartbeats_failed",
                    "heartbeats_late",
                    "writes_sent",
                    "writes_rejected",
                    "writes_rate_limited",
                    "recoveries",
                ):
                    value = counters.get(name)
                    if type(value) is int and value >= 0:
                        setattr(diagnostics, name, value)
                write_errors = set(client.snapshot.get("write_errors", []))
                if write_errors != diagnostics.write_errors:
                    diagnostics.write_errors = write_errors
                    async_check_repairs(hass, entry)

            client.on_update = update_remote
            update_remote()
        elif not control.read_only:
            entry.runtime_data.heartbeat = _start_heartbeat(
                hass, entry, client, diagnostics
            )
        await coordinator.async_config_entry_first_refresh()
        validate_vehicle_selection()
        if automatic_selection_available(entry.options):
            # Before the car's rules start: they rest for an unconfirmed car.
            entry.runtime_data.vehicle_binding = VehicleBinding(hass, entry)
            await entry.runtime_data.vehicle_binding.async_start()
        await _async_start_vehicle(hass, entry, options, timed_reset_complete, validate_vehicle_selection)
        if entry.runtime_data.soc_goal is not None:
            # The first poll must never retry a restored old vehicle's stop
            # before its source has been rebound or deliberately cleared.
            coordinator.retry_soc_goal = entry.runtime_data.soc_goal.retry_stop
        if remote:
            # The service owns its own durable goal. Old direct-mode intent must
            # not return when this entry later switches back from the service.
            await GoalStore(hass, entry.entry_id, "").async_remove()
            await SocGoalStore(hass, entry.entry_id, "").async_remove()
            await SessionHistoryStore(hass, entry.entry_id, "").async_remove()
        platforms = tuple(_entry_platforms(entry))
        await hass.config_entries.async_forward_entry_setups(
            entry, platforms
        )
        entry.runtime_data.loaded_platforms = platforms
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
        if entry.runtime_data.plug_charge is not None:
            entry.runtime_data.plug_charge.start()
        if entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True:
            detector = VehicleDetectionObserver(hass, entry)
            entry.runtime_data.vehicle_detection = detector
            detector.start()
        if cost_enabled and needs_year_refresh(dict(entry.options)):
            # A stale tariff is refreshed after setup, so the wallbox and its
            # heartbeat are already running before any external HTTP request.
            entry.async_create_background_task(
                hass, async_refresh_tariff_year(hass, entry),
                "amtron_tariff_refresh", eager_start=False,
            )
        if cost_enabled and entry.options.get(CONF_AUTO_TARIFF_REFRESH) is True:
            @callback
            def refresh_new_tariff_year(_now: object) -> None:
                entry.async_create_background_task(
                    hass, async_refresh_tariff_year(hass, entry),
                    "amtron_tariff_refresh", eager_start=False,
                )

            entry.async_on_unload(async_track_tariff_midnight(hass, refresh_new_tariff_year))
        async_setup_repairs(hass, entry)
        if not control.read_only and not remote:
            entry.runtime_data.recovery = HeartbeatRecovery(hass, entry)
            entry.runtime_data.recovery.start()
    except BaseException:
        try:
            await _async_stop_runtime(entry.runtime_data)
        finally:
            forget_loss_attribution(hass, entry.entry_id)
        raise
    _remember_vehicle(entry)
    if handoff is not None:
        handoff.setup_complete = True
    return True


def _trip_memory(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> VehicleTripMemory:
    return VehicleTripMemory(hass, entry.entry_id, entry.runtime_data.identity.serial_number or "")


async def _async_park_trip(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
    """Remember the selected car's open trip before another car is selected."""
    runtime = entry.runtime_data
    if runtime.vehicle_profile_id is None or entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is not True:
        return
    # A deleted profile's trip is dropped rather than kept for no car.
    trip = (
        runtime.timed_goal.goal
        if runtime.timed_goal is not None and runtime.vehicle_profile_id in profiles(entry.options)
        else None
    )
    await _trip_memory(hass, entry).async_swap(runtime.vehicle_profile_id, trip, None)


async def _async_resume_trip(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, timed_reset_complete: bool,
) -> None:
    """Give a newly selected car back its own open trip, or none at all."""
    # Any car can be edited without selecting it. A car whose planning was
    # switched off loses its parked trip, so re-enabling never revives it.
    await _trip_memory(hass, entry).async_retain({
        key for key in profiles(entry.options)
        if vehicle_soc_is_configured(each := profile_options(entry.options, key))
        and timed_goal_is_enabled(each)
    })
    options = vehicle_options(entry.options)
    selected = options.get(CONF_VEHICLE_PROFILE_ID)
    if (
        not timed_reset_complete or selected is None
        or entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is not True
        or not vehicle_soc_is_configured(options) or not timed_goal_is_enabled(options)
    ):
        return
    identity = entry.runtime_data.identity.serial_number or ""
    trip = await _trip_memory(hass, entry).async_swap(None, None, selected)
    if trip is None:
        return
    try:
        await TimedGoalStore(hass, entry.entry_id, identity).async_save(trip)
    except OSError as err:
        _LOGGER.warning("Vehicle departure not resumed: %s", err)


async def _async_cancel_old_trip(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, timed_reset_complete: bool,
) -> None:
    """Withdraw a disabled or replaced trip's current before a car starts."""
    runtime = entry.runtime_data
    if (
        vehicle_options(entry.options).get(CONF_TIMED_GOAL_ENABLED) is False
        or entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is True
        or not timed_reset_complete
    ):
        try:
            # Reclaim only this entry's work after an unreachable unload;
            # manual requests and other owners remain authoritative.
            await runtime.control.async_cancel_timed_current(f"timed:{entry.entry_id}")
        except (AmtronBusError, HomeAssistantError) as err:
            _raise_optional_setup_error(runtime.client, err, "timed_goal_unavailable")
        if timed_reset_complete and CONF_TIMED_GOAL_RESET_PENDING in entry.options:
            stored_options = dict(entry.options)
            stored_options.pop(CONF_TIMED_GOAL_RESET_PENDING)
            # Complete both halves of the reset before consuming its
            # authority. A failed reconnect must still cancel the old
            # service queue on the next attempt, even after re-enabling.
            hass.config_entries.async_update_entry(entry, options=stored_options)


async def _async_start_vehicle(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, options: dict[str, Any],
    timed_reset_complete: bool, validate_vehicle_selection: Callable[[], None],
) -> None:
    """Start the observers that belong to the selected vehicle."""
    runtime = entry.runtime_data
    control, coordinator, client = runtime.control, runtime.coordinator, runtime.client
    loss_enabled = loss_vehicle_options(hass, entry).get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED) != LOSS_SOURCE_DISABLED
    if loss_enabled:
        runtime.charge_loss = ChargeLossObserver(hass, entry)
        await runtime.charge_loss.async_start()
    validate_vehicle_selection()
    runtime.vehicle_telemetry = resolve_telemetry_source(hass, options)
    if vehicle_soc_is_configured(options) or control.soc_goal.source is not None:
        memory = VehicleTargetMemory(hass, entry.entry_id, runtime.identity.serial_number or "")
        await memory.async_load()
        observer = VehicleSocObserver(
            hass, control, coordinator,
            options.get(CONF_VEHICLE_SOC_ENTITY),
            options.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY),
            telemetry=runtime.vehicle_telemetry,
            use_vehicle_soc=options.get(CONF_VEHICLE_SOC_USE_PROFILE) is True,
            use_vehicle_limit=options.get(CONF_VEHICLE_LIMIT_USE_PROFILE) is True,
            profile_id=options.get(CONF_VEHICLE_PROFILE_ID),
            memory=memory,
            binding=runtime.vehicle_binding,
        )
        runtime.vehicle_soc = observer
        try:
            await observer.async_start()
        except (AmtronBusError, HomeAssistantError, OSError) as err:
            _raise_optional_setup_error(client, err, "soc_goal_unavailable")
    validate_vehicle_selection()
    if (
        timed_reset_complete and vehicle_soc_is_configured(options)
        and timed_goal_is_enabled(options)
    ):
        timed = TimedGoalObserver(hass, entry)
        runtime.timed_goal = timed
        try:
            await timed.async_start()
        except (OSError, ValueError) as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="timed_goal_state_invalid"
            ) from err
        except (AmtronBusError, HomeAssistantError) as err:
            _raise_optional_setup_error(client, err, "timed_goal_unavailable")
    runtime.vehicle_profile_id = options.get(CONF_VEHICLE_PROFILE_ID)


async def _async_stop_vehicle(
    runtime: MennekesAmtronRuntimeData, *, withdraw: bool = False,
) -> None:
    """Stop the selected vehicle's observers; the wallbox keeps running.

    ``withdraw`` ends the car's plan pause when another car takes over.
    """
    runtime.vehicle_profile_id = None
    timed, soc, loss = runtime.timed_goal, runtime.vehicle_soc, runtime.charge_loss
    runtime.timed_goal = runtime.vehicle_soc = runtime.charge_loss = None
    runtime.vehicle_telemetry = None
    if timed is not None:
        await timed.async_stop(withdraw=withdraw)
    for owner in (soc, loss):
        if owner is not None:
            await owner.async_stop()


def _remember_vehicle(entry: MennekesAmtronConfigEntry) -> None:
    """Record what the running observers were started for."""
    runtime = entry.runtime_data
    runtime.vehicle_base_options = without_selection(entry.options)
    runtime.vehicle_selection = {
        key: entry.options.get(key) for key in SELECTION_KEYS if key != CONF_TIMED_GOAL_RESET_PENDING
    }


async def _async_switch_vehicle(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry,
) -> bool:
    """Apply a pure vehicle selection change without reloading entities.

    Returns False when anything else changed or the switch failed; the caller
    then reloads the entry, which retries the new vehicle's setup.
    """
    runtime = entry.runtime_data
    if (
        runtime.entry_data != dict(entry.data)
        or runtime.vehicle_base_options != without_selection(entry.options)
    ):
        return False
    selection = {
        key: entry.options.get(key) for key in SELECTION_KEYS if key != CONF_TIMED_GOAL_RESET_PENDING
    }
    if selection == runtime.vehicle_selection and entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is not True:
        return True
    # Like at setup, a poll during the switch must not retry the previous
    # car's stop before the new car's source has been bound or cleared.
    retry = runtime.coordinator.retry_soc_goal
    runtime.coordinator.retry_soc_goal = None
    runtime.vehicle_switching = True
    try:
        await _async_park_trip(hass, entry)
        await _async_stop_vehicle(runtime, withdraw=True)
        auto = entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True
        if not auto and runtime.vehicle_detection is not None:
            runtime.vehicle_detection.stop()
            runtime.vehicle_detection = None
        timed_reset_complete = await _async_reset_disabled_trip(hass, entry)
        await _async_resume_trip(hass, entry, timed_reset_complete)
        await _async_cancel_old_trip(hass, entry, timed_reset_complete)
        options = vehicle_options(entry.options)
        await _async_start_vehicle(hass, entry, options, timed_reset_complete, lambda: None)
        if auto and runtime.vehicle_detection is None:
            runtime.vehicle_detection = VehicleDetectionObserver(hass, entry)
            runtime.vehicle_detection.start()
    except asyncio.CancelledError:
        # A cancelled switch leaves the observers half stopped and its caller
        # never sees the result: only a reload brings the car's rules back.
        hass.config_entries.async_schedule_reload(entry.entry_id)
        raise
    except Exception:
        # Any failure falls back to a full reload, which retries the setup.
        _LOGGER.debug("Vehicle switch needs a reload", exc_info=True)
        await _async_stop_vehicle(runtime)
        return False
    finally:
        runtime.vehicle_switching = False
    runtime.coordinator.retry_soc_goal = retry
    runtime.vehicle_selection = selection
    runtime.coordinator.async_update_listeners()
    return True


def _raise_optional_setup_error(
    client: MennekesModbusClient, err: Exception, translation_key: str,
) -> NoReturn:
    """Retry transient source setup failures while preserving reauthentication."""
    if isinstance(err, ConfigEntryAuthFailed):
        raise err
    if isinstance(client, RemoteClient) and client.failure_reason == "service_auth_failed":
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="service_auth_failed"
        ) from err
    if isinstance(err, ConfigEntryNotReady):
        raise err
    raise ConfigEntryNotReady(
        translation_domain=DOMAIN, translation_key=translation_key
    ) from err


async def _async_reset_disabled_trip(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> bool:
    """Remove the saved trip; retain reset authority until queues are cancelled."""
    if (
        vehicle_options(entry.options).get(CONF_TIMED_GOAL_ENABLED) is not False
        and entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is not True
    ):
        return True
    try:
        await TimedGoalStore(hass, entry.entry_id, "").async_remove()
    except OSError:
        # Keep this authority in the entry, rather than only in process memory.
        # Re-enabling after a restart still requires removal of the old trip.
        if entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is not True:
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_TIMED_GOAL_RESET_PENDING: True},
            )
        _LOGGER.warning("Departure planning remains disabled because its old trip could not be cleared")
        return False
    return True


def _entry_serial_binding(entry: MennekesAmtronConfigEntry) -> str | None:
    """Return a known serial, retaining old endpoint-only entry identities."""
    serial = entry.data.get(CONF_SERIAL_NUMBER)
    if isinstance(serial, str) and serial:
        return serial
    unique_id = getattr(entry, "unique_id", None)
    if isinstance(unique_id, str) and unique_id and not unique_id.startswith(f"{DOMAIN}_"):
        return unique_id
    return None


async def async_unload_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> bool:
    """Tear down one wallbox."""

    runtime_data = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, runtime_data.loaded_platforms
    )
    if not unloaded:
        return False
    retain_client = runtime_data.retain_client_on_unload
    retain_cost = runtime_data.retain_cost_on_unload
    if retain_client or retain_cost or runtime_data.retain_attribution_on_unload:
        handoff = hass.data[f"{DOMAIN}_reload_clients"][entry.entry_id]
        handoff.unloaded = True
        handoff.client = runtime_data.client if retain_client else None
        handoff.sessions = runtime_data.sessions if retain_client else None
        handoff.charging_cost = runtime_data.charging_cost if retain_cost else None
    try:
        await _async_stop_runtime(runtime_data, close_client=not retain_client, retain_cost=retain_cost)
    finally:
        if not runtime_data.retain_attribution_on_unload:
            forget_loss_attribution(hass, entry.entry_id)
    return unloaded


async def _async_stop_runtime(
    runtime: MennekesAmtronRuntimeData, *, close_client: bool = True, retain_cost: bool = False,
) -> None:
    """Release the same owners after partial setup and ordinary unloading.

    Detach source and polling listeners before their consumers, and keep the
    heartbeat until the charging controllers have finished their persistence.
    The command owner and physical connection are always closed last.
    """
    if runtime.vehicle_detection is not None:
        runtime.vehicle_detection.stop()
    if runtime.vehicle_binding is not None:
        runtime.vehicle_binding.stop()
    if runtime.loss_attribution_unsubscribe is not None:
        runtime.loss_attribution_unsubscribe()
        runtime.loss_attribution_unsubscribe = None
    if runtime.plug_charge is not None:
        await runtime.plug_charge.async_stop()
    runtime.coordinator.observe_plug_charge = None
    runtime.coordinator.observe_session = None
    if runtime.charge_loss is not None:
        await runtime.charge_loss.async_stop()
    if runtime.charging_cost is not None and not retain_cost:
        await runtime.charging_cost.async_stop()
    if runtime.sessions is not None and close_client:
        await runtime.sessions.async_close()
    for owner in (
        runtime.timed_goal, runtime.vehicle_soc, runtime.soc_goal,
        runtime.goal, runtime.recovery, runtime.heartbeat,
    ):
        if owner is not None:
            await owner.async_stop()
    await runtime.control.async_shutdown()
    if close_client:
        await runtime.client.async_close()


async def async_reload_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> None:
    """Reload the entry after its options changed."""

    locks: dict[str, asyncio.Lock] = hass.data.setdefault(f"{DOMAIN}_reload_locks", {})
    async with locks.setdefault(entry.entry_id, asyncio.Lock()):
        await _async_reload_entry_locked(hass, entry)


async def _async_reload_entry_locked(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry,
) -> None:
    """Inspect the current owner only after earlier option reloads finish."""

    runtime = getattr(entry, "runtime_data", None)
    cost = runtime.charging_cost if runtime is not None else None
    if runtime is not None:
        if (
            runtime.plug_charge is not None and runtime.plug_charge.options_applied() is True
        ) or (cost is not None and cost.apply_tariff_only_update()):
            # Applied in place. The same update may also have changed the
            # vehicle, which must still be switched, not silently skipped.
            runtime.vehicle_base_options = without_selection(entry.options)
        if await _async_switch_vehicle(hass, entry):
            return
        # A reload that also selects another car keeps the previous car's trip.
        await _async_park_trip(hass, entry)
    # Automatic profile changes reload optional observers. Preserve the open
    # physical connection so this logical reload does not discard the current
    # cable observation or an already granted session. Real disconnects still
    # invalidate the gate in the driver. Other reloads retain their lifecycle.
    retain = (
        runtime is not None and CONF_SERVER_URL not in entry.data
        and not isinstance(runtime.client, RemoteClient)
        and runtime.client.connected
        and (runtime.control.plug_charge.get("enabled") is True or runtime.sessions is not None)
        and runtime.client.config == _connection_config(entry)
    )
    if runtime is not None and retain:
        runtime.retain_client_on_unload = True
    remote = CONF_SERVER_URL in entry.data
    retain_cost = cost is not None and (remote or retain) and cost.retain_for_reload(entry, remote=remote)
    retain_attribution = runtime is not None and (remote or retain) and runtime.entry_data == dict(entry.data)
    if runtime is not None:
        runtime.retain_cost_on_unload = retain_cost
        runtime.retain_attribution_on_unload = retain_attribution
    if runtime is not None and (retain or retain_cost or retain_attribution):
        handoff = RuntimeHandoff(
            identity=runtime.identity,
            connection_generation=runtime.client.connection_generation if retain else None,
        )
        # Store the pre-unload transport generation. Unload may await storage
        # while a library-managed reconnect changes observation continuity.
        hass.data.setdefault(f"{DOMAIN}_reload_clients", {})[entry.entry_id] = handoff
    try:
        await hass.config_entries.async_reload(entry.entry_id)
    finally:
        if runtime is not None and (retain or retain_cost or retain_attribution):
            runtime.retain_client_on_unload = False
            runtime.retain_cost_on_unload = False
            runtime.retain_attribution_on_unload = False
            unused = hass.data.get(f"{DOMAIN}_reload_clients", {}).pop(entry.entry_id, None)
            if unused is not None:
                try:
                    await unused.async_close()
                finally:
                    if unused.unloaded and not unused.setup_complete:
                        forget_loss_attribution(hass, entry.entry_id)


async def async_remove_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> None:
    """Remove repairs owned by a deleted entry, also on HA before 2026.11."""

    hass.data.get(f"{DOMAIN}_write_states", {}).pop(entry.entry_id, None)
    hass.data.get(f"{DOMAIN}_reload_locks", {}).pop(entry.entry_id, None)
    forget_loss_attribution(hass, entry.entry_id)
    await GoalStore(hass, entry.entry_id, "").async_remove()
    await SocGoalStore(hass, entry.entry_id, "").async_remove()
    await VehicleTargetStore(hass, entry.entry_id, "").async_remove()
    await TimedGoalStore(hass, entry.entry_id, "").async_remove()
    await VehicleTripStore(hass, entry.entry_id, "").async_remove()
    await VehiclePhaseStore(hass, entry.entry_id, "").async_remove()
    await VehicleBindingStore(hass, entry.entry_id, "").async_remove()
    await SessionHistoryStore(hass, entry.entry_id, "").async_remove()
    await LossStore(hass, entry.entry_id, "").async_remove()
    await async_prune_profile_losses(hass, entry.entry_id, ())
    await ModelStore(hass, entry.entry_id, "", kind="charging_cost", model=LastChargingCost).async_remove()
    prefix = f"{entry.entry_id}_"
    for domain, issue_id in tuple(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix):
            ir.async_delete_issue(hass, domain, issue_id)


def _entry_platforms(entry: MennekesAmtronConfigEntry) -> list[Platform]:
    """Return the platforms this entry exposes.

    Direct read-only entries omit controls. Service entries retain their control
    entities and follow the service's live write permission through availability.
    """

    platforms = list(READ_PLATFORMS)
    if CONF_SERVER_URL in entry.data or control_mode_is_master(dict(entry.options)):
        platforms.extend(CONTROL_PLATFORMS)
    else:
        if any(
            options.get(CONF_CHARGE_LOSS_SOURCE) == LOSS_SOURCE_SENSOR
            for options in each_vehicle_options(entry.options)
        ):
            platforms.append(Platform.BUTTON)
        if profiles(entry.options):
            platforms.append(Platform.SELECT)
    return platforms


def _connection_config(entry: MennekesAmtronConfigEntry) -> ConnectionConfig:
    """Load direct transport settings, retaining serial as the legacy default."""

    data = entry.data
    connection_type = data.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL)
    if connection_type != CONNECTION_SERIAL:
        return NetworkConfig(
            host=data.get(CONF_HOST, ""),
            tcp_port=data.get(CONF_TCP_PORT, DEFAULT_TCP_PORT),
            connection_type=connection_type,
            device_id=data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            tcp_authentication=data.get(CONF_TCP_AUTHENTICATION, False),
            tcp_auth_password=data.get(CONF_TCP_AUTH_PASSWORD, ""),
        )
    return _serial_config(entry)


def _serial_config(entry: MennekesAmtronConfigEntry) -> SerialConfig:
    data = entry.data
    return SerialConfig(
        port=str(data[CONF_PORT]),
        baudrate=int(data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)),
        bytesize=int(data.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)),
        parity=str(data.get(CONF_PARITY, DEFAULT_PARITY)),
        stopbits=int(data.get(CONF_STOPBITS, DEFAULT_STOPBITS)),
        device_id=int(data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)),
    )


def _scan_interval(entry: MennekesAmtronConfigEntry) -> float:
    value = entry.options.get(CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL_SECONDS)
    return float(value)


def _control_options(entry: MennekesAmtronConfigEntry) -> ControlOptions:
    options = dict(entry.options)
    cap = options.get(CONF_CURRENT_LIMIT)
    return ControlOptions(
        read_only=CONF_SERVER_URL not in entry.data and not control_mode_is_master(options),
        current_cap=float(cap) if isinstance(cap, (int, float)) else None,
        state_change_interval_seconds=float(options.get(
            CONF_STATE_CHANGE_INTERVAL_SECONDS, DEFAULT_STATE_CHANGE_INTERVAL_SECONDS
        )),
        phase_switch_grace_seconds=float(options.get(
            CONF_PHASE_SWITCH_GRACE_SECONDS, DEFAULT_PHASE_SWITCH_GRACE_SECONDS
        )),
    )


def _start_heartbeat(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    client: MennekesModbusClient,
    diagnostics: WriteDiagnostics,
) -> HeartbeatTask:
    """Start the heartbeat as an entry-owned background task.

    Home Assistant cancels entry background tasks on unload, which is what
    keeps a stale heartbeat from holding the bus after the entry is gone.
    """

    heartbeat = HeartbeatTask(client, diagnostics)
    task = entry.async_create_background_task(
        hass, heartbeat.async_run(), "mennekes_amtron_rtu_heartbeat"
    )
    heartbeat.attach(task)
    return heartbeat


__all__ = ["WallboxData", "async_setup", "async_setup_entry", "async_unload_entry"]
