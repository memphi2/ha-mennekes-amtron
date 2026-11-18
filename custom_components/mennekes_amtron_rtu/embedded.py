"""Run the wallbox service inside Home Assistant for a direct connection.

A direct entry gets the same runtime the app runs, reached without HTTP. It
is kept through a reload of the entry, so the heartbeat never pauses for a
changed option, and stopped when the entry is unloaded for any other reason.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later

from .device.client_errors import AmtronBusError
from .device.const import (
    CHARGING_CURRENT_MINIMUM,
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PHASE_SWITCH_GRACE_SECONDS,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
    DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    DOMAIN,
)
from .device.errors import DeviceError
from .device.notice import FailureNotice
from .device.service_api import ServiceApi
from .device.service_config import ServerConfig
from .device.service_departure_runtime import DepartureModbusService
from .device.waveshare_auth import WaveshareAuthenticationRejectedError
from .entry_connection import connection_config
from .options_flow import control_mode_is_master
from .service_transport import EmbeddedTransport

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)

_HOSTS = f"{DOMAIN}_embedded"
_RELOADING = f"{DOMAIN}_reloading"
_DEFERRED = f"{DOMAIN}_deferred_release"
_RESTARTS = f"{DOMAIN}_restarts"
_RESTART_TIMERS = f"{DOMAIN}_restart_timers"
_RESTART_NOTICES = f"{DOMAIN}_restart_notices"
_RETRYING = (ConfigEntryState.SETUP_RETRY, ConfigEntryState.SETUP_IN_PROGRESS)
# How often a service kept for a retried setup asks whether the retry is still due.
_RETRY_CHECK_SECONDS = 30
# A service that ran this long before it ended starts again at once.
_STABLE_SECONDS = 600
# Reading the identity over a slow serial line with retries takes a while.
_START_SECONDS = 60
# Changed limits wait for the command in progress, not for a hung bus.
_APPLY_SECONDS = 5


def embedded_config(entry: MennekesAmtronConfigEntry) -> ServerConfig:
    """Translate a direct entry into the settings the service runs with."""
    options = entry.options
    cap = options.get(CONF_CURRENT_LIMIT)

    def seconds(key: str, default: float) -> float:
        return float(options.get(key, default))

    return ServerConfig(
        connection_config(entry),
        token="",
        read_only=not control_mode_is_master(dict(options)),
        # Without a limit of its own the entry follows the wallbox's maximum,
        # and only the minimum until the wallbox has said what that is.
        current_cap=float(cap) if isinstance(cap, (int, float)) else CHARGING_CURRENT_MINIMUM,
        cap_from_wallbox=not isinstance(cap, (int, float)),
        scan_interval=seconds(CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL_SECONDS),
        measurement_interval=seconds(
            CONF_MEASUREMENT_INTERVAL_SECONDS, DEFAULT_MEASUREMENT_INTERVAL_SECONDS
        ),
        state_change_interval_seconds=seconds(
            CONF_STATE_CHANGE_INTERVAL_SECONDS, DEFAULT_STATE_CHANGE_INTERVAL_SECONDS
        ),
        phase_switch_grace_seconds=seconds(
            CONF_PHASE_SWITCH_GRACE_SECONDS, DEFAULT_PHASE_SWITCH_GRACE_SECONDS
        ),
    )


def state_directory(hass: HomeAssistant, entry_id: str) -> Path:
    return Path(hass.config.path(".storage", f"{DOMAIN}.{entry_id}"))


class EmbeddedService:
    """One running service and the way to reach it."""

    def __init__(
        self, config: ServerConfig, directory: Path, expected_serial: str | None = None,
    ) -> None:
        self.config = config
        self._service = DepartureModbusService(config, directory / "intent.json")
        # Bound before the first read: another wallbox on this connection is
        # rejected before the service writes anything to it, heartbeat included.
        self._service.client.bind_identity(expected_serial)
        self.transport = EmbeddedTransport(ServiceApi(self._service))
        self._stopping = False
        self._died = False
        self._stop_lock = asyncio.Lock()
        self._stopped = False
        # Called when the service ends on its own, which it does rather than
        # carry on without its heartbeat.
        self.on_ended: Callable[[str, BaseException | None], None] | None = None

    async def async_start(self) -> None:
        """Start and wait for the wallbox; a failed start leaves nothing running."""
        task = self._service.start()
        try:
            async with asyncio.timeout(_START_SECONDS):
                await self._service.wait_started()
        except BaseException as err:
            died = task.done() and not task.cancelled()
            if died:
                # It ended by itself, and the caller gets that cause: the
                # stop must not raise it a second time in its place.
                self._died = True
            await self.async_stop()
            if died and isinstance(err, Exception) and not isinstance(
                err, (AmtronBusError, DeviceError, OSError, TimeoutError, ValueError)
            ):
                # Unforeseen, but no reason never to try again.
                _LOGGER.error(
                    "Service: stopped while starting (%s)", type(err).__name__, exc_info=err,
                )
                raise DeviceError("service_unavailable") from err
            raise
        task.add_done_callback(self._ended)

    def _ended(self, task: asyncio.Task[None]) -> None:
        if self._stopping or task.cancelled():
            return
        self._died = True
        error = task.exception()
        while isinstance(error, BaseExceptionGroup) and error.exceptions:
            error = error.exceptions[0]
        reason = type(error).__name__ if error is not None else "no error"
        if self.on_ended is not None:
            self.on_ended(reason, error)
        else:
            _LOGGER.error("Service: stopped unexpectedly (%s)", reason)

    def async_on_polled(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after every poll of the wallbox; returns its removal."""
        self._service.on_polled = listener

        def remove() -> None:
            if self._service.on_polled is listener:
                self._service.on_polled = None

        return remove

    @property
    def ending(self) -> bool:
        """Whether it ended on its own or is being stopped."""
        return self._stopping or self._died

    @property
    def stopped(self) -> bool:
        """Whether its connection is closed."""
        return self._stopped

    def credentials_rejected(self) -> bool:
        """Whether the gateway refused the saved password on the newest poll."""
        return isinstance(self._service.failure, WaveshareAuthenticationRejectedError)

    def fits(self, config: ServerConfig) -> bool:
        """Whether the new settings can be applied without a new connection."""
        return (
            config.connection == self.config.connection
            and config.read_only == self.config.read_only
        )

    async def async_apply(self, config: ServerConfig) -> None:
        await self._service.apply_runtime_settings(config)
        self.config = replace(
            config, connection=self.config.connection, read_only=self.config.read_only
        )

    async def async_stop(self) -> None:
        """Stop once; whoever else asks waits until the connection is closed."""
        self._stopping = True
        async with self._stop_lock:
            if self._stopped:
                return
            try:
                # A command whose caller gave up still finishes all its writes
                # before the connection closes under it.
                await self.transport.async_drain()
            finally:
                try:
                    await self._service.stop()
                except (OSError, ValueError) as err:
                    # The last save failed. The connection is closed all the
                    # same, and an unload must not fail over a full disk.
                    _LOGGER.warning(
                        "Service: state not saved while stopping (%s)", type(err).__name__
                    )
                except Exception:
                    # Its end was reported when it happened; everything was
                    # still saved and closed.
                    if not self._died:
                        self._stopped = True
                        raise
                # A stop cancelled half-way is not one: whoever asks next
                # finishes it before anyone else opens the bus.
                self._stopped = True


async def _async_stop_registered(
    hosts: dict[str, EmbeddedService], entry_id: str, host: EmbeddedService,
) -> None:
    """Stop a service and only then give up its place.

    Until its connection is closed, nobody else may open the bus.
    """
    try:
        await host.async_stop()
    finally:
        # A stop cancelled half-way keeps the place for whoever finishes it.
        if host.stopped and hosts.get(entry_id) is host:
            del hosts[entry_id]


def _hosts(hass: HomeAssistant) -> dict[str, EmbeddedService]:
    if _HOSTS not in hass.data:
        hass.data[_HOSTS] = {}

        async def stop_all(_event: Event) -> None:
            # Home Assistant stops without unloading its entries. Stopping
            # here lets every service save what it knows and end its heartbeat.
            hosts = hass.data[_HOSTS]
            for entry_id, host in list(hosts.items()):
                try:
                    await _async_stop_registered(hosts, entry_id, host)
                except Exception:
                    # The other wallboxes still get their orderly stop.
                    _LOGGER.exception("Service: stop failed")

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_all)
    hosts: dict[str, EmbeddedService] = hass.data[_HOSTS]
    return hosts


async def async_acquire(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, expected_serial: str | None = None,
) -> EmbeddedService:
    """Return the entry's running service, started or adjusted as needed.

    ``expected_serial`` binds a new service to the configured wallbox.
    """
    if hass.is_stopping:
        # Every service was just stopped; nobody would stop a new one.
        raise DeviceError("service_unavailable")
    config = embedded_config(entry)
    hosts = _hosts(hass)
    entry_id = entry.entry_id
    _cancel_deferred_release(hass, entry_id)
    # A service stays registered while it is adjusted or stopped, so a
    # shutdown still finds it and nobody opens the bus beside it.
    host = hosts.get(entry_id)
    if host is not None and host.ending:
        # It ended or is being stopped: wait until its connection is closed.
        await _async_stop_registered(hosts, entry_id, host)
        host = None
    if host is not None:
        if host.fits(config):
            try:
                async with asyncio.timeout(_APPLY_SECONDS):
                    await host.async_apply(config)
            except (DeviceError, TimeoutError):
                # Not ready for new limits: a fresh start applies them, unless
                # Home Assistant is stopping and the service went with it.
                if host.ending:
                    raise DeviceError("service_unavailable") from None
            except BaseException:
                # Never leave a running service that nothing can stop.
                await _async_stop_registered(hosts, entry_id, host)
                raise
            else:
                if host.ending:
                    # Stopped meanwhile, by a shutdown or by its own end.
                    raise DeviceError("service_unavailable")
                return host
        await _async_stop_registered(hosts, entry_id, host)
    host = EmbeddedService(config, state_directory(hass, entry.entry_id), expected_serial)
    # Registered before it starts: a shutdown during a slow start stops it.
    hosts[entry_id] = host
    try:
        await host.async_start()
    except BaseException:
        if hosts.get(entry_id) is host:
            del hosts[entry_id]
        raise
    if host.ending or hosts.get(entry_id) is not host:
        # Home Assistant stopped it while it started.
        raise DeviceError("service_unavailable")

    def ended(reason: str, error: BaseException | None = None) -> None:
        if hosts.get(entry_id) is not host:
            return
        notices: dict[str, FailureNotice] = hass.data.setdefault(_RESTART_NOTICES, {})
        notice = notices.setdefault(entry_id, FailureNotice(_LOGGER, "Service"))
        delay = _restart_delay(hass, entry_id)
        if not delay:
            # It ran steadily since the last time: this is a new episode.
            notice.recovered("ran steadily again")
        # One report per cause, however often it ends for it.
        notice.failed(
            "stopped unexpectedly; starting it again"
            + (f" in {delay:.0f} seconds" if delay else ""),
            reason, level=logging.ERROR, exc_info=error if error is not None else False,
        )

        @callback
        def reload(_now: datetime | None = None) -> None:
            hass.data.get(_RESTART_TIMERS, {}).pop(entry_id, None)
            # Whoever started a service meanwhile has reloaded already, and
            # an entry removed or disabled since then wants none.
            if (
                entry_id not in hosts and not hass.is_stopping
                and hass.config_entries.async_get_entry(entry_id) is entry
                and entry.disabled_by is None
            ):
                hass.config_entries.async_schedule_reload(entry_id)

        async def restart() -> None:
            try:
                await _async_stop_registered(hosts, entry_id, host)
            finally:
                if delay:
                    _cancel_restart(hass, entry_id)
                    hass.data.setdefault(_RESTART_TIMERS, {})[entry_id] = async_call_later(
                        hass, delay, reload
                    )
                else:
                    reload()

        hass.async_create_task(restart(), "amtron_service_restart")

    host.on_ended = ended
    return host


def _restart_delay(hass: HomeAssistant, entry_id: str) -> float:
    """Wait longer before each restart of a service that keeps ending.

    A reload succeeds whatever ends the service afterwards, so Home
    Assistant's own retry delay never applies.
    """
    restarts: dict[str, tuple[int, float]] = hass.data.setdefault(_RESTARTS, {})
    now = monotonic()
    count, restarted_at = restarts.get(entry_id, (0, now))
    if now - restarted_at > _STABLE_SECONDS:
        count = 0
    delay = 0.0 if count == 0 else float(min(5 * 2**count, _STABLE_SECONDS))
    restarts[entry_id] = (count + 1, now + delay)
    return delay


def _cancel_deferred_release(hass: HomeAssistant, entry_id: str) -> None:
    cancel = hass.data.get(_DEFERRED, {}).pop(entry_id, None)
    if cancel is not None:
        cancel()


def _cancel_restart(hass: HomeAssistant, entry_id: str) -> None:
    cancel = hass.data.get(_RESTART_TIMERS, {}).pop(entry_id, None)
    if cancel is not None:
        cancel()


@callback
def async_release_unless_retried(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
    """Keep a healthy service through a setup that will be tried again.

    A setup can fail for reasons that have nothing to do with the wallbox.
    The heartbeat carries on for as long as Home Assistant has a retry
    planned, however long it waits; an entry that is disabled or otherwise
    out of retries stops the service.
    """
    entry_id = entry.entry_id
    _cancel_deferred_release(hass, entry_id)
    watches: dict[str, Callable[[], None]] = hass.data.setdefault(_DEFERRED, {})
    timer: Callable[[], None] | None = None

    def stop_watching() -> None:
        unsubscribe()
        if timer is not None:
            timer()

    async def check(_now: datetime | None = None) -> None:
        nonlocal timer
        if watches.get(entry_id) is not stop_watching:
            return
        if timer is not None:
            timer()
        if entry.state not in _RETRYING and entry.setup_lock.locked():
            # A reload passes through "not loaded" under the lock, and so
            # does a disable: wait for whichever it is to finish.
            async with entry.setup_lock:
                pass
            if watches.get(entry_id) is not stop_watching:
                return
        # The failed setup itself is still in progress when this is asked first.
        if entry.state in _RETRYING:
            timer = async_call_later(hass, _RETRY_CHECK_SECONDS, check)
        else:
            timer = None
            await async_release(hass, entry_id, force=True)

    @callback
    def changed(_change: object, changed_entry: object) -> None:
        # Told at once where Home Assistant says so; the timer covers the rest.
        if changed_entry is entry and entry.state not in _RETRYING:
            hass.async_create_task(check(), "amtron_service_release")

    unsubscribe = async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, changed)
    timer = async_call_later(hass, _RETRY_CHECK_SECONDS, check)
    watches[entry_id] = stop_watching


def mark_reloading(hass: HomeAssistant, entry_id: str, reloading: bool) -> None:
    marks: set[str] = hass.data.setdefault(_RELOADING, set())
    (marks.add if reloading else marks.discard)(entry_id)


async def async_release(hass: HomeAssistant, entry_id: str, *, force: bool = False) -> None:
    """Stop the entry's service unless a reload is about to take it over."""
    if not force and entry_id in hass.data.get(_RELOADING, ()):
        return
    _cancel_deferred_release(hass, entry_id)
    _cancel_restart(hass, entry_id)
    hosts = _hosts(hass)
    host = hosts.get(entry_id)
    if host is not None:
        await _async_stop_registered(hosts, entry_id, host)


async def async_leave(hass: HomeAssistant, entry_id: str) -> None:
    """Forget the service of an entry that now uses the app, if it had one."""
    directory = state_directory(hass, entry_id)
    if entry_id in _hosts(hass) or await hass.async_add_executor_job(directory.exists):
        await async_forget(hass, entry_id)


async def async_forget(hass: HomeAssistant, entry_id: str) -> None:
    """Stop the service of a removed entry and delete what it saved."""
    await async_release(hass, entry_id, force=True)
    hass.data.get(_RELOADING, set()).discard(entry_id)
    hass.data.get(_RESTARTS, {}).pop(entry_id, None)
    hass.data.get(_RESTART_NOTICES, {}).pop(entry_id, None)
    await hass.async_add_executor_job(
        lambda: shutil.rmtree(state_directory(hass, entry_id), ignore_errors=True)
    )
