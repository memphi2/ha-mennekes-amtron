from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron.diagnostics import (
    async_get_config_entry_diagnostics,
    decoded_values,
    fnv1a64,
)
from tests.ha_fakes import build_runtime


def test_the_serial_hash_is_stable_and_not_the_serial() -> None:
    digest = fnv1a64("ABC123456789")
    assert digest == fnv1a64("ABC123456789")
    assert digest != fnv1a64("ABC123456788")
    assert "ABC123456789" not in str(digest)
    assert fnv1a64(None) is None
    assert fnv1a64("") is None


def test_decoded_values_hide_identifying_strings() -> None:
    dump = decoded_values(
        {
            "evse_state": 5,
            "serial_number": "ABC123456789",
            "firmware_version": "2023",
            "article_number": "1313201205",
            "not_a_register": 1,
        }
    )
    assert dump == {"0x0100 evse_state": 5}


def test_the_dump_carries_no_port_path_and_no_serial_number() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        dump = await async_get_config_entry_diagnostics(hass, entry)
        text = repr(dump)
        assert "/dev/fake" not in text
        assert "ABC123456789" not in text
        assert dump["bus"]["port_category"] == "other"
        assert dump["bus"]["device_id"] == 50
        assert dump["device"]["layout_version"] == "v01.03"
        assert dump["device"]["serial_number_hash"]
        assert dump["control"]["read_only"] is False
        assert dump["control"]["heartbeat_running"] is False
        assert dump["poll"]["last_update_success"] is True
        assert "0x0100 evse_state" in dump["values"]
        assert dump["registers"]["status"]["0x0100"] == 1

    asyncio.run(run())


def test_a_failing_block_is_reported_in_the_raw_image() -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        transport.read_exceptions[0x1000] = 0x04
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert "error" in dump["registers"]["statistics"]

    asyncio.run(run())
