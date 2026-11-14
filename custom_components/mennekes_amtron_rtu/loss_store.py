"""Bounded loss estimates stored outside Home Assistant Recorder."""

import re
from collections.abc import Collection
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import STORAGE_DIR

from .device.charge_loss import LossStatistics
from .device.const import DOMAIN
from .goal_store import ModelStore

_PROFILE_ID = re.compile(r"[0-9a-f]{32}")


class LossStore(ModelStore[LossStatistics]):
    """Retain energy sums and one correction slot for a wallbox."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, identity: str,
        *, profile_id: str | None = None,
    ) -> None:
        if profile_id is not None and _PROFILE_ID.fullmatch(profile_id) is None:
            raise ValueError("invalid vehicle profile storage key")
        storage_id = (
            entry_id if profile_id is None
            else f"{entry_id}.{profile_id}"
        )
        super().__init__(hass, storage_id, identity, kind="charge_loss", model=LossStatistics)


async def async_prune_profile_losses(
    hass: HomeAssistant, entry_id: str, retained: Collection[str],
) -> None:
    """Remove deleted profiles after their old runtime has finished saving.

    Keep the unsuffixed aggregate for manually configured sources. Only exact owned profile
    file names are removed; other wallboxes and unrelated storage are untouched.
    """
    prefix = f"{DOMAIN}.charge_loss.{entry_id}."
    directory = Path(hass.config.path(STORAGE_DIR))

    def prune() -> None:
        if not directory.is_dir():
            return
        for path in directory.iterdir():
            if not path.name.startswith(prefix):
                continue
            profile_id = path.name[len(prefix):]
            if _PROFILE_ID.fullmatch(profile_id) is not None and profile_id not in retained:
                path.unlink(missing_ok=True)

    await hass.async_add_executor_job(prune)
