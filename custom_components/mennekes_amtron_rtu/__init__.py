"""The MENNEKES AMTRON integration.

This module is a thin facade: it assembles the runtime data of one config
entry and tears it down again. Every rule about the device lives in the
focused modules next to it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from copy import deepcopy
from typing import Any, NoReturn

from homeassistant.config_entries import ConfigEntryState
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
)
from .charge_loss_observer import ChargeLossObserver
from .charging_cost_const import (
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_COST_SOURCE,
    COST_SOURCE_DISABLED,
)
from .coordinator import ServiceCoordinator
from .cost_observer import ChargingCostObserver, LastChargingCost
from .data import (
    MennekesAmtronRuntimeData,
    RuntimeHandoff,
    WallboxData,
)
from .device.client_errors import AmtronBusError, AmtronIdentityError
from .device.const import (
    CONF_RELEASE_OFF_ON_UNPLUG,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_SERIAL_NUMBER,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)
from .device.errors import DeviceError
from .device.notice import FailureNotice
from .device.waveshare_auth import WaveshareAuthenticationRejectedError
from .embedded import (
    EmbeddedService,
    async_acquire,
    async_forget,
    async_leave,
    async_release,
    async_release_unless_retried,
    mark_reloading,
)
from .entry_types import MennekesAmtronConfigEntry
from .goal_store import (
    DepartureIntent,
    ModelStore,
    TimedGoalStore,
)
from .guest_charging import GuestCharging
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
from .repairs import async_check_repairs, async_setup_repairs
from .service_client import (
    CONF_SERVER_TOKEN,
    CONF_SERVER_URL,
    ServiceClient,
    server_url,
)
from .service_control import ServiceControl, ServiceHeartbeat
from .service_errors import ServiceConnectionError
from .service_transport import HttpTransport, ServiceTransport
from .services import async_setup_services
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
                _LOGGER.warning("Charging loss: deleted vehicle storage not removed; cleanup retries on setup")
    remote = CONF_SERVER_URL in entry.data
    expected_serial = _entry_serial_binding(entry)
    handoff: RuntimeHandoff | None = hass.data.get(f"{DOMAIN}_reload_clients", {}).get(entry.entry_id)
    client: ServiceClient | None = None
    credentials_rejected: Callable[[], bool] | None = None
    host: EmbeddedService | None = None
    connected = False
    try:
        if remote:
            transport: ServiceTransport = HttpTransport(
                async_get_clientsession(hass),
                server_url(str(entry.data[CONF_SERVER_URL])),
                str(entry.data[CONF_SERVER_TOKEN]),
            )
            # An entry that was direct until now must give up the bus first.
            await async_release(hass, entry.entry_id, force=True)
        else:
            host = await async_acquire(hass, entry, expected_serial)
            transport = host.transport
            credentials_rejected = host.credentials_rejected
        client = ServiceClient(transport=transport)
        client.bind_identity(expected_serial)
        await client.async_connect()
        identity = client.identity
        if expected_serial is not None and identity.serial_number != expected_serial:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="device_identity_changed"
            )
        # An entry without a saved serial binds to the wallbox it just read.
        client.bind_identity(identity.serial_number)
        connected = True
        if remote:
            # The app is the right one and answers: what the entry's own
            # service saved is not the app's state. Until then it is kept for
            # a way back.
            await async_leave(hass, entry.entry_id)
        await _async_hand_over_unplug_rule(client, entry, controls=remote or control_mode_is_master(dict(entry.options)))
        validate_vehicle_selection()
    except BaseException as err:
        if client is not None:
            await client.async_close()
        if not remote:
            await _async_release_after_failure(hass, entry, err, healthy=connected)
        if isinstance(err, AmtronIdentityError):
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="device_identity_changed"
            ) from err
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
        if isinstance(err, (AmtronBusError, DeviceError, OSError, TimeoutError, ValueError)):
            raise ConfigEntryNotReady(str(err)) from err
        raise

    coordinator = ServiceCoordinator(
        hass, entry, client=client,
        # The embedded service says when it has polled; the timer only covers
        # a poll that never reports.
        scan_interval=_scan_interval(entry) * (1 if host is None else 2),
        credentials_rejected=credentials_rejected,
    )
    if host is not None:
        push: asyncio.Task[None] | None = None

        def polled() -> None:
            nonlocal push
            # The service outlives a reload; a runtime being taken down or
            # not yet up fetches nothing.
            if (
                entry.state is ConfigEntryState.LOADED
                and getattr(getattr(entry, "runtime_data", None), "coordinator", None) is coordinator
                and (push is None or push.done())
            ):
                # The entry's own: an unload cancels a fetch still under way.
                push = entry.async_create_background_task(
                    hass, coordinator.async_refresh(), "amtron_state_push",
                )

        entry.async_on_unload(host.async_on_polled(polled))

    def notify_pending() -> None:
        # Cancelling queued work or changing a goal is not a successful poll.
        # Notify entities without clearing a connection failure or reading the bus.
        if coordinator.data is not None:
            coordinator.data.service_control = deepcopy(
                client.snapshot.get("control", coordinator.data.service_control)
            )
            coordinator.data.pending_change_until = control.pending_until
        coordinator.async_update_listeners()

    control = ServiceControl(
        client,
        state=coordinator.current_data,
        request_refresh=coordinator.async_refresh,
        notify_pending=notify_pending,
    )
    guest = GuestCharging(client, coordinator.async_refresh)
    entry.runtime_data = MennekesAmtronRuntimeData(
        client=client,
        coordinator=coordinator,
        control=control,
        guest=guest,
        identity=identity,
        entry_data=dict(entry.data),
        controls=remote or control_mode_is_master(dict(entry.options)),
    )
    # Whether a failure below is something other than a silent wallbox.
    wallbox_answers = True
    try:
        # A reconnect is only a gap; the session's presence rule judges it.
        if handoff is not None and handoff.identity != identity:
            await handoff.async_close()
            forget_loss_attribution(hass, entry.entry_id)
        if profiles(entry.options):
            entry.runtime_data.loss_attribution_unsubscribe = track_loss_attribution(hass, entry)
        validate_vehicle_selection()
        # Configure the gate before starting heartbeat/recovery. The app owns
        # the previous grant; an unchanged policy survives this HA reload.
        plug_enabled = entry.options.get(CONF_VIRTUAL_PLUG_CHARGE) is True
        if plug_enabled and control.read_only:
            # Enabling needs write access; retrying setup would never get it.
            # The gate keeps its last state until writing is allowed again.
            _LOGGER.warning("Plug & Charge: not applied while the wallbox is read-only")
        elif not _service_intent_rejected(client) and (plug_enabled or (
            CONF_VIRTUAL_PLUG_CHARGE in entry.options
            and control.plug_charge.get("enabled") is True
        )):
            try:
                await control.async_configure_plug_charge(
                    plug_enabled, policy=plug_charge_policy(entry.options),
                )
            except (AmtronBusError, HomeAssistantError) as err:
                _raise_optional_setup_error(client, err, "plug_charge_unavailable")
        if entry.runtime_data.controls and not _service_intent_rejected(client):
            entry.runtime_data.plug_charge = PlugChargeObserver(hass, entry)
        await _async_resume_trip(hass, entry, timed_reset_complete)
        await _async_cancel_old_trip(hass, entry, timed_reset_complete)
        cost_enabled = entry.options.get(CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED) != COST_SOURCE_DISABLED
        if _estimates_enabled(entry):
            if cost_enabled:
                retained_cost = handoff.charging_cost if handoff is not None else None
                if handoff is not None and retained_cost is not None and retained_cost.retain_for_reload(entry):
                    retained_cost.rebind(entry)
                    entry.runtime_data.charging_cost = retained_cost
                    handoff.charging_cost = None
                else:
                    if handoff is not None and retained_cost is not None:
                        await retained_cost.async_stop()
                        handoff.charging_cost = None
                    entry.runtime_data.charging_cost = ChargingCostObserver(hass, entry)
                    await entry.runtime_data.charging_cost.async_start()

            # Failed polls arrive as None. A reconnect re-checks the wallbox
            # identity; the session judges continuity, costs follow it.
            async def observe_estimates(data: WallboxData | None) -> None:
                if entry.runtime_data.charging_cost is not None:
                    await entry.runtime_data.charging_cost.async_observe(data)

            coordinator.observe_session = observe_estimates
        client.on_auth_failed = lambda: entry.async_start_reauth(hass)
        heartbeat = ServiceHeartbeat(client)
        entry.runtime_data.heartbeat = heartbeat

        intent_rejected = _service_intent_rejected(client)

        known_write_errors: tuple[str, ...] = ()

        def update_remote() -> None:
            nonlocal intent_rejected, known_write_errors
            rejected = _service_intent_rejected(client)
            if rejected != intent_rejected and entry.state is ConfigEntryState.LOADED:
                # A change seen while the entry is still being set up waits
                # for the first update after it: only a loaded entry reloads.
                intent_rejected = rejected
                # Rebuild deferred vehicle owners after the reset, including
                # one acknowledged through another HA or API client. The
                # service and its heartbeat stay.
                hass.async_create_task(
                    async_reload_entry(hass, entry, force=True), "amtron_reload"
                )
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
            write_errors = tuple(client.snapshot.get("write_errors", ()))
            if write_errors != known_write_errors:
                known_write_errors = write_errors
                async_check_repairs(hass, entry)

        client.on_update = update_remote
        update_remote()
        try:
            await coordinator.async_config_entry_first_refresh()
        except BaseException:
            wallbox_answers = False
            raise
        validate_vehicle_selection()
        if automatic_selection_available(entry.options):
            # Before the car's rules start: they rest for an unconfirmed car.
            entry.runtime_data.vehicle_binding = VehicleBinding(hass, entry)
            await entry.runtime_data.vehicle_binding.async_start()
        await _async_start_vehicle(hass, entry, options, timed_reset_complete, validate_vehicle_selection)
        platforms = tuple(_entry_platforms(entry))
        await hass.config_entries.async_forward_entry_setups(
            entry, platforms
        )
        entry.runtime_data.loaded_platforms = platforms
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
        if entry.runtime_data.plug_charge is not None:
            entry.runtime_data.plug_charge.start()
        if entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True and not _service_intent_rejected(client):
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
        _keep_unplug_rule_handed_over(hass, entry, controls=entry.runtime_data.controls)
    except BaseException as err:
        try:
            try:
                await _async_stop_runtime(entry.runtime_data)
            finally:
                # Whatever else fails, no service is left that nothing stops.
                if not remote:
                    await _async_release_after_failure(
                        hass, entry, err, healthy=wallbox_answers,
                    )
        finally:
            forget_loss_attribution(hass, entry.entry_id)
        raise
    _remember_vehicle(entry)
    if handoff is not None:
        handoff.setup_complete = True
    return True


def _estimates_enabled(entry: MennekesAmtronConfigEntry) -> bool:
    """Whether any cost or loss estimate follows the charging sessions."""
    return (
        entry.options.get(CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED) != COST_SOURCE_DISABLED
        or vehicle_options(entry.options).get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED) != LOSS_SOURCE_DISABLED
        or any(
            profile.options.get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED) != LOSS_SOURCE_DISABLED
            for profile in profiles(entry.options).values()
        )
    )


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
    if _service_intent_rejected(entry.runtime_data.client):
        return
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
        await TimedGoalStore(hass, entry.entry_id, identity).async_save(DepartureIntent(trip, pending=True))
    except OSError as err:
        _LOGGER.warning("Departure plan: vehicle departure not resumed (%s)", err)


async def _async_cancel_old_trip(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, timed_reset_complete: bool,
) -> None:
    """Withdraw a disabled or replaced trip's current before a car starts."""
    runtime = entry.runtime_data
    if _service_intent_rejected(runtime.client):
        return
    if (
        vehicle_options(entry.options).get(CONF_TIMED_GOAL_ENABLED) is False
        or entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is True
        or not timed_reset_complete
    ) and timed_reset_complete and CONF_TIMED_GOAL_RESET_PENDING in entry.options:
        if entry.options.get(CONF_TIMED_GOAL_RESET_PENDING) is True:
            try:
                # An unload that could not reach the service left it planning
                # the old trip; withdrawing it also ends that plan's pause.
                await runtime.control.async_set_departure_trip(None, None, withdraw=True)
            except (AmtronBusError, HomeAssistantError) as err:
                _raise_optional_setup_error(runtime.client, err, "timed_goal_unavailable")
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
    # A repair must remain reachable even if source binding/configuration is
    # deliberately refused by the quarantined service. Rebuild after reset.
    if _service_intent_rejected(runtime.client):
        return
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
        _service_intent_rejected(runtime.client)
        or runtime.entry_data != dict(entry.data)
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
        if runtime.vehicle_binding is not None:
            # Its rest state still judges the previous car until the next poll.
            runtime.vehicle_binding.refresh()
        await _async_start_vehicle(hass, entry, options, timed_reset_complete, lambda: None)
        if auto and runtime.vehicle_detection is None:
            runtime.vehicle_detection = VehicleDetectionObserver(hass, entry)
            runtime.vehicle_detection.start()
    except asyncio.CancelledError:
        # A cancelled switch leaves the observers half stopped and its caller
        # never sees the result: only a reload brings the car's rules back.
        # An unload or HA shutdown cancels it too; those must stay down.
        if entry.state is ConfigEntryState.LOADED and not hass.is_stopping:
            hass.async_create_task(
                async_reload_entry(hass, entry, force=True), "amtron_reload"
            )
        raise
    except Exception:
        # Any failure falls back to a full reload, which retries the setup.
        _LOGGER.debug("Vehicle switch needs a reload", exc_info=True)
        await _async_stop_vehicle(runtime)
        return False
    finally:
        runtime.vehicle_switching = False
    runtime.vehicle_selection = selection
    runtime.coordinator.async_update_listeners()
    return True


async def _async_release_after_failure(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, err: BaseException, *, healthy: bool,
) -> None:
    """Stop the embedded service unless only something else failed.

    A setup Home Assistant tries again while the wallbox answers, as over a
    car's settings, keeps the heartbeat until then.
    """
    if healthy and isinstance(err, ConfigEntryNotReady):
        async_release_unless_retried(hass, entry)
    else:
        await async_release(hass, entry.entry_id, force=True)


def _keep_unplug_rule_handed_over(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, *, controls: bool,
) -> None:
    """Hand the option over again when the service lost it, e.g. a reinstalled app."""
    runtime = entry.runtime_data
    notice = FailureNotice(_LOGGER, "Control")
    busy: list[asyncio.Task[None]] = []

    async def hand_over() -> None:
        try:
            await _async_hand_over_unplug_rule(runtime.client, entry, controls=controls)
        except (DeviceError, HomeAssistantError) as err:
            notice.failed(
                "switching the release off after unplugging not handed to the service",
                getattr(err, "translation_key", None) or type(err).__name__,
            )
        else:
            notice.recovered("switching the release off after unplugging handed to the service")

    def compare() -> None:
        wanted = controls and entry.options.get(CONF_RELEASE_OFF_ON_UNPLUG) is True
        if (
            entry.state is ConfigEntryState.LOADED
            and not (busy and not busy[0].done())
            and runtime.client.connected
            and runtime.client.snapshot.get("control", {}).get("release_off_on_unplug") is not wanted
        ):
            busy[:] = [entry.async_create_background_task(hass, hand_over(), "amtron_unplug_rule")]

    entry.async_on_unload(runtime.coordinator.async_add_listener(compare))


async def _async_hand_over_unplug_rule(client: ServiceClient, entry: MennekesAmtronConfigEntry, *, controls: bool) -> None:
    """Tell the service whether to switch the release off after an unplug.

    The service that owns the bus applies the rule. After a rejected saved
    state it takes no setting; the reload that follows hands it over.
    """
    wanted = controls and entry.options.get(CONF_RELEASE_OFF_ON_UNPLUG) is True
    if (
        client.snapshot.get("control", {}).get("release_off_on_unplug") is not wanted
        and not _service_intent_rejected(client)
    ):
        await client.execute("unplug_release_config", value=wanted)


def _service_intent_rejected(client: ServiceClient) -> bool:
    return client.snapshot.get("control", {}).get("intent_rejected") is True


def _raise_optional_setup_error(
    client: ServiceClient, err: Exception, translation_key: str,
) -> NoReturn:
    """Retry transient source setup failures while preserving reauthentication."""
    if isinstance(err, ConfigEntryAuthFailed):
        raise err
    if client.failure_reason == "service_auth_failed":
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
        _LOGGER.warning("Departure plan: stays disabled because its old trip could not be cleared")
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
    retain_cost = runtime_data.retain_cost_on_unload
    if retain_cost or runtime_data.retain_attribution_on_unload:
        handoff = hass.data[f"{DOMAIN}_reload_clients"][entry.entry_id]
        handoff.unloaded = True
        handoff.charging_cost = runtime_data.charging_cost if retain_cost else None
    # A reload that also selects another car must end the old car's plan
    # pause, like an in-place switch; a plain reload or shutdown keeps it.
    previous = runtime_data.vehicle_profile_id
    options = vehicle_options(entry.options)
    withdraw = previous is not None and options.get(CONF_VEHICLE_PROFILE_ID) != previous
    # So does switching the car's departure planning off: the app would
    # otherwise keep planning a trip that Home Assistant no longer shows.
    withdraw = withdraw or (
        runtime_data.timed_goal is not None
        and not (vehicle_soc_is_configured(options) and timed_goal_is_enabled(options))
    )
    try:
        try:
            await _async_stop_runtime(runtime_data, retain_cost=retain_cost, withdraw_plan=withdraw)
        finally:
            # Last: the car's observers above still withdraw through it.
            await async_release(hass, entry.entry_id)
    finally:
        if not runtime_data.retain_attribution_on_unload:
            forget_loss_attribution(hass, entry.entry_id)
    return unloaded


async def _async_stop_runtime(
    runtime: MennekesAmtronRuntimeData, *, retain_cost: bool = False,
    withdraw_plan: bool = False,
) -> None:
    """Release the same owners after partial setup and ordinary unloading.

    Detach source and polling listeners before their consumers; the client
    is closed last.
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
    runtime.coordinator.observe_session = None
    if runtime.charge_loss is not None:
        await runtime.charge_loss.async_stop()
    if runtime.charging_cost is not None and not retain_cost:
        await runtime.charging_cost.async_stop()
    if runtime.timed_goal is not None:
        await runtime.timed_goal.async_stop(withdraw=withdraw_plan)
    if runtime.vehicle_soc is not None:
        await runtime.vehicle_soc.async_stop()
    await runtime.client.async_close()


async def async_reload_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, *, force: bool = False,
) -> None:
    """Reload the entry after its options changed.

    ``force`` reloads although no option changed, for a runtime that has to
    be built again; the embedded service is kept either way.
    """

    locks: dict[str, asyncio.Lock] = hass.data.setdefault(f"{DOMAIN}_reload_locks", {})
    async with locks.setdefault(entry.entry_id, asyncio.Lock()):
        await _async_reload_entry_locked(hass, entry, force)


async def _async_reload_entry_locked(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, force: bool = False,
) -> None:
    """Inspect the current owner only after earlier option reloads finish."""

    runtime = getattr(entry, "runtime_data", None)
    cost = runtime.charging_cost if runtime is not None else None
    if runtime is not None and not force:
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
    retain_cost = cost is not None and cost.retain_for_reload(entry)
    retain_attribution = runtime is not None and runtime.entry_data == dict(entry.data)
    if runtime is not None:
        runtime.retain_cost_on_unload = retain_cost
        runtime.retain_attribution_on_unload = retain_attribution
    if runtime is not None and (retain_cost or retain_attribution):
        handoff = RuntimeHandoff(identity=runtime.identity)
        hass.data.setdefault(f"{DOMAIN}_reload_clients", {})[entry.entry_id] = handoff
    # The embedded service keeps its connection and heartbeat through this.
    mark_reloading(hass, entry.entry_id, True)
    try:
        await hass.config_entries.async_reload(entry.entry_id)
    finally:
        mark_reloading(hass, entry.entry_id, False)
        if entry.state not in (
            ConfigEntryState.LOADED, ConfigEntryState.SETUP_RETRY, ConfigEntryState.FAILED_UNLOAD,
        ):
            # A setup that is tried again has decided about the service itself,
            # and an entry that could not be unloaded still runs on it.
            await async_release(hass, entry.entry_id)
        if runtime is not None and (retain_cost or retain_attribution):
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

    hass.data.get(f"{DOMAIN}_reload_locks", {}).pop(entry.entry_id, None)
    forget_loss_attribution(hass, entry.entry_id)
    await async_forget(hass, entry.entry_id)
    await VehicleTargetStore(hass, entry.entry_id, "").async_remove()
    await TimedGoalStore(hass, entry.entry_id, "").async_remove()
    await VehicleTripStore(hass, entry.entry_id, "").async_remove()
    await VehiclePhaseStore(hass, entry.entry_id, "").async_remove()
    await VehicleBindingStore(hass, entry.entry_id, "").async_remove()
    await LossStore(hass, entry.entry_id, "").async_remove()
    await async_prune_profile_losses(hass, entry.entry_id, ())
    await ModelStore(hass, entry.entry_id, "", kind="charging_cost", model=LastChargingCost).async_remove()
    prefix = f"{entry.entry_id}_"
    for domain, issue_id in tuple(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix):
            ir.async_delete_issue(hass, domain, issue_id)


def _entry_platforms(entry: MennekesAmtronConfigEntry) -> list[Platform]:
    """Return the platforms this entry exposes.

    A direct connection set to read only shows no controls. With the app, the
    controls stay and follow the app's write permission through availability.
    """

    platforms = list(READ_PLATFORMS)
    if CONF_SERVER_URL in entry.data or control_mode_is_master(dict(entry.options)):
        platforms.extend(CONTROL_PLATFORMS)
    elif profiles(entry.options):
        # The vehicle choice is a setting of the entry, not a wallbox control.
        platforms.append(Platform.SELECT)
    return platforms


def _scan_interval(entry: MennekesAmtronConfigEntry) -> float:
    # With the app, HA fetches its status at the default pace; the app's own
    # settings decide how often the wallbox is read.
    if CONF_SERVER_URL in entry.data:
        return float(DEFAULT_SCAN_INTERVAL_SECONDS)
    value = entry.options.get(CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL_SECONDS)
    return float(value)


__all__ = ["WallboxData", "async_setup", "async_setup_entry", "async_unload_entry"]
