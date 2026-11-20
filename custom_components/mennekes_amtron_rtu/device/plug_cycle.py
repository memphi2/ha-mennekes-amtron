"""The one account of the car at the wallbox: plugged in or not, and how surely."""

from __future__ import annotations

import hashlib
import math
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .data import WallboxData
from .registers import CP_STATE, EVSE_STATE
from .session_mark import SessionMark
from .vehicle import metered_vehicle_present, vehicle_connected

# An unknown state is not an unplug. Brief read gaps and transient EVSE/CP
# combinations keep a connection; a longer one may hide another car.
UNKNOWN_GRACE_SECONDS = 60.0
_CONTEXT = hashlib.sha256(b"physical-connection").hexdigest()


@dataclass(frozen=True, slots=True)
class PlugChange:
    """What one observation changed."""

    # A confirmed unplug ended a connection.
    ended: bool = False


class PlugCycle:
    """Follow the physical connection for everything that depends on it.

    A connection lasts from a confirmed plug-in to a confirmed unplug and has
    one ``id``. Unknown for longer than the grace, it has ``lapsed``: nobody
    can tell whether the same car is still there, so whatever was permitted
    for it ends, and the ``scope`` it had is not given again.
    """

    def __init__(self) -> None:
        self.forget()

    def forget(self) -> None:
        """Nothing carries over, e.g. to a client that may reach another wallbox."""
        # The newest evidence: read in that poll, never a held value.
        self.present: bool | None = None
        # For energy estimates only: a connected CP proves a car while the
        # EVSE state still lags at plug-in.
        self.metered: bool | None = None
        self.id: str | None = None
        self.since: datetime | None = None
        self.lapsed = False
        # The wallbox's own record of this plug-in, as far as read.
        self.reading: dict[str, Any] | None = None
        self._absent_at: datetime | None = None
        self._unknown_since: float | None = None
        self._epoch = 0

    def read(
        self, data: WallboxData | None, layout_version: int, *,
        at: datetime | None = None, now: float | None = None,
    ) -> PlugChange:
        """Take one poll, or None for one that failed."""
        if data is None:
            self.metered = None
            return self.observe(None, at=at, now=now)
        cp_supported = layout_version >= CP_STATE.min_layout
        self.metered = metered_vehicle_present(data.get, cp_supported=cp_supported)
        change = self.observe(
            vehicle_connected(data.fresh(EVSE_STATE.key), data.fresh(CP_STATE.key), cp_supported=cp_supported),
            at=at, now=now,
        )
        if self.scope is not None:
            self._note_reading(data)
        return change

    def observe(
        self, present: bool | None, *, at: datetime | None = None, now: float | None = None,
    ) -> PlugChange:
        """Take one piece of evidence, from a poll or a read inside a command."""
        at = at or datetime.now(UTC)
        now = time.monotonic() if now is None else now
        self.present = present
        if present is False:
            ended = self.id is not None
            self.id = self.since = self.reading = self._unknown_since = None
            self.lapsed = False
            self._absent_at = at
            return PlugChange(ended=ended)
        if present is None:
            if self.id is not None and not self.lapsed:
                if self._unknown_since is None:
                    self._unknown_since = now
                elif now - self._unknown_since >= UNKNOWN_GRACE_SECONDS:
                    self.lapsed, self.reading = True, None
            return PlugChange()
        self._unknown_since = None
        if self.id is None:
            self.id = secrets.token_hex(16)
            self.since = self._absent_at or at
        if self.lapsed:
            self.lapsed = False
            self._epoch += 1
        return PlugChange()

    def suspect(self, now: float | None = None) -> None:
        """Treat the state as unknown from now, e.g. after a reconnect."""
        if self.id is not None and self._unknown_since is None:
            self._unknown_since = time.monotonic() if now is None else now

    @property
    def scope(self) -> str | None:
        """An opaque name for the connection as far as it is surely the same one."""
        if self.id is None or self.lapsed:
            return None
        return hashlib.sha256(f"{self.id}|{self._epoch}".encode()).hexdigest()

    @property
    def mark(self) -> SessionMark | None:
        """The wallbox's record of this plug-in, None until read in full."""
        reading = self.reading
        if reading is None or not reading.keys() >= {"at", "duration_s", "energy_kwh", "count"}:
            return None
        return SessionMark(reading["at"], reading["duration_s"], reading["energy_kwh"], reading["count"], _CONTEXT)

    def _note_reading(self, data: WallboxData) -> None:
        reading = dict(self.reading or {})
        duration, energy = data.fresh("session_duration"), data.fresh("session_energy")
        if (
            data.session_read and type(duration) is int and duration >= 0
            and isinstance(energy, float) and math.isfinite(energy) and energy >= 0
        ):
            reading.update(at=datetime.now(UTC), duration_s=duration, energy_kwh=energy)
        count = data.fresh("sessions_total")
        if data.energy_read and type(count) is int and count >= 0:
            reading["count"] = count
        self.reading = reading
