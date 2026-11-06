"""Bounded loss estimates stored outside Home Assistant Recorder."""

from homeassistant.core import HomeAssistant

from .device.charge_loss import LossStatistics
from .goal_store import ModelStore


class LossStore(ModelStore[LossStatistics]):
    """Retain energy sums and one correction slot for a wallbox."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="charge_loss", model=LossStatistics)
