from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.mennekes_amtron import config_flow
from custom_components.mennekes_amtron._flow_connect import async_test_connection
from custom_components.mennekes_amtron._flow_search import SearchResult
from custom_components.mennekes_amtron.client import SerialConfig
from custom_components.mennekes_amtron.client_errors import (
    AmtronBusError,
    AmtronConnectionError,
)
from custom_components.mennekes_amtron.config_flow import (
    MennekesAmtronConfigFlow,
    _entry_title,
    _serial_config,
)
from custom_components.mennekes_amtron.const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICE_ID,
    CONF_FRAME,
    CONF_PARITY,
    CONF_PORT,
    CONF_SEARCH,
    CONF_SERIAL_NUMBER,
    CONF_STOPBITS,
)
from custom_components.mennekes_amtron.data import DeviceIdentity
from custom_components.mennekes_amtron.options_flow import MennekesAmtronOptionsFlow
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass

USER_INPUT = {
    CONF_PORT: " /dev/fake ",
    CONF_DEVICE_ID: 50,
    CONF_BAUDRATE: 57600,
    CONF_FRAME: "8N2",
}
IDENTITY = DeviceIdentity(
    layout_version=0x0103,
    serial_number="ABC123456789",
    article_number="1313201205",
)


class Flow(MennekesAmtronConfigFlow):
    """A config flow with the Home Assistant flow manager stubbed out."""

    def __init__(self, hass: FakeHass) -> None:
        self.hass = hass
        self.set_unique_id: str | None = None
        self.aborted: list[str] = []
        self.reconfigure_entry = FakeConfigEntry()
        self.updates: dict[str, Any] | None = None

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}

    async def async_set_unique_id(self, unique_id: str) -> None:
        self.set_unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        self.aborted.append("configured")

    def _abort_if_unique_id_mismatch(self) -> None:
        self.aborted.append("mismatch")

    def _get_reconfigure_entry(self) -> FakeConfigEntry:
        return self.reconfigure_entry

    def async_update_reload_and_abort(
        self, entry: Any, *, data_updates: dict[str, Any]
    ) -> dict[str, Any]:
        self.updates = data_updates
        return {"type": "abort", "reason": "reconfigure_successful"}


def _patch_connection(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def fake(_config: SerialConfig) -> DeviceIdentity:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(config_flow, "async_test_connection", fake)


def test_serial_input_becomes_a_trimmed_serial_config() -> None:
    config = _serial_config(USER_INPUT)
    assert config.port == "/dev/fake"
    assert config.device_id == 50
    assert config.bytesize == 8
    assert config.parity == "N"
    assert config.stopbits == 2


def test_a_stored_entry_still_describes_its_bus() -> None:
    """Entries created before the frame field keep their three keys."""

    config = _serial_config(
        {
            CONF_PORT: "/dev/fake",
            CONF_DEVICE_ID: 23,
            CONF_BAUDRATE: 19200,
            CONF_BYTESIZE: 8,
            CONF_PARITY: "E",
            CONF_STOPBITS: 1,
        }
    )
    assert (config.bytesize, config.parity, config.stopbits) == (8, "E", 1)


def test_every_offered_frame_round_trips() -> None:
    from custom_components.mennekes_amtron.const import (
        SUPPORTED_FRAMES,
        frame_label,
        frame_parts,
    )

    for frame in SUPPORTED_FRAMES:
        assert frame_label(*frame_parts(frame)) == frame
        config = _serial_config({**USER_INPUT, CONF_FRAME: frame})
        assert frame_label(config.bytesize, config.parity, config.stopbits) == frame


def test_the_offered_frames_are_the_ones_the_device_documents() -> None:
    from custom_components.mennekes_amtron.const import (
        SUPPORTED_FRAMES,
        SUPPORTED_PARITY_STOPBITS,
        frame_parts,
    )

    assert {frame_parts(frame)[1:] for frame in SUPPORTED_FRAMES} == set(
        SUPPORTED_PARITY_STOPBITS
    )


def test_the_entry_title_carries_no_serial_number() -> None:
    """The title becomes the device name, and that prefixes every entity id."""

    assert _entry_title(IDENTITY) == "MENNEKES AMTRON"
    assert _entry_title(DeviceIdentity()) == "MENNEKES AMTRON"
    assert IDENTITY.serial_number not in _entry_title(IDENTITY)
    assert IDENTITY.article_number not in _entry_title(IDENTITY)


def test_the_first_step_shows_a_form() -> None:
    async def run() -> None:
        result = await Flow(FakeHass()).async_step_user()
        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["description_placeholders"]["validated_layout"] == "v01.03"

    asyncio.run(run())


def test_a_good_connection_creates_the_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, IDENTITY)
        flow = Flow(FakeHass())
        result = await flow.async_step_user(dict(USER_INPUT))
        assert result["type"] == "create_entry"
        assert result["data"][CONF_SERIAL_NUMBER] == "ABC123456789"
        assert flow.set_unique_id == "ABC123456789"
        assert flow.aborted == ["configured"]

    asyncio.run(run())


def test_a_device_without_a_serial_number_falls_back_to_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, DeviceIdentity(layout_version=0x0100))
        flow = Flow(FakeHass())
        result = await flow.async_step_user(dict(USER_INPUT))
        assert CONF_SERIAL_NUMBER not in result["data"]
        assert flow.set_unique_id == "mennekes_amtron_/dev/fake"
        assert result["data"][CONF_PORT] == "/dev/fake"

    asyncio.run(run())


def test_a_dead_port_shows_cannot_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, AmtronConnectionError("no port"))
        result = await Flow(FakeHass()).async_step_user(dict(USER_INPUT))
        assert result["errors"] == {"base": "cannot_connect"}

    asyncio.run(run())


def test_a_failed_attempt_keeps_what_the_user_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong baud rate must not throw the whole form away."""

    async def run() -> None:
        _patch_connection(monkeypatch, AmtronConnectionError("no port"))
        typed = {**USER_INPUT, CONF_DEVICE_ID: 23, CONF_BAUDRATE: 19200}
        result = await Flow(FakeHass()).async_step_user(dict(typed))
        assert _suggested(result) == {
            CONF_PORT: " /dev/fake ",
            CONF_DEVICE_ID: 23,
            CONF_BAUDRATE: 19200,
            CONF_FRAME: "8N2",
        }

    asyncio.run(run())


def test_reconfigure_starts_from_the_entry_not_the_factory_defaults() -> None:
    async def run() -> None:
        flow = Flow(FakeHass())
        flow.reconfigure_entry = FakeConfigEntry(
            data={
                CONF_PORT: "/dev/serial/by-id/adapter",
                CONF_DEVICE_ID: 23,
                CONF_BAUDRATE: 19200,
                CONF_BYTESIZE: 8,
                CONF_PARITY: "E",
                CONF_STOPBITS: 1,
            }
        )
        result = await flow.async_step_reconfigure()
        suggested = _suggested(result)
        assert suggested[CONF_DEVICE_ID] == 23
        assert suggested[CONF_BAUDRATE] == 19200
        # the entry stores the parts, the form asks for the frame
        assert suggested[CONF_FRAME] == "8E1"
        assert suggested[CONF_PORT] == "/dev/serial/by-id/adapter"

    asyncio.run(run())


def _suggested(result: dict[str, Any]) -> dict[str, Any]:
    """Return the values a form is pre-filled with."""

    return {
        str(marker): marker.description["suggested_value"]
        for marker in result["data_schema"].schema
        if marker.description and "suggested_value" in marker.description
    }


def test_a_silent_device_shows_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, AmtronBusError("no layout"))
        result = await Flow(FakeHass()).async_step_user(dict(USER_INPUT))
        assert result["errors"] == {"base": "invalid_response"}

    asyncio.run(run())


def test_reconfigure_updates_the_bus_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, IDENTITY)
        flow = Flow(FakeHass())
        form = await flow.async_step_reconfigure()
        assert form["step_id"] == "reconfigure"
        result = await flow.async_step_reconfigure(dict(USER_INPUT))
        assert result["reason"] == "reconfigure_successful"
        assert flow.updates[CONF_DEVICE_ID] == 50
        assert flow.aborted == ["mismatch"]

    asyncio.run(run())


def test_the_options_flow_is_offered() -> None:
    assert isinstance(
        MennekesAmtronConfigFlow.async_get_options_flow(FakeConfigEntry()),
        MennekesAmtronOptionsFlow,
    )


def test_the_connection_test_reads_the_device_and_closes_the_port() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        from custom_components.mennekes_amtron import _flow_connect

        original = _flow_connect.MennekesModbusClient

        def factory(config: SerialConfig) -> object:
            return original(config, client_factory=lambda _c: transport)

        _flow_connect.MennekesModbusClient = factory  # type: ignore[assignment]
        try:
            identity = await async_test_connection(SerialConfig(port="/dev/fake"))
        finally:
            _flow_connect.MennekesModbusClient = original  # type: ignore[assignment]
        assert identity.serial_number == "ABC123456789"
        assert transport.close_calls == 1

    asyncio.run(run())


class SearchFlow(Flow):
    """A flow whose progress and menu results are inspectable."""

    def __init__(self, hass: FakeHass) -> None:
        MennekesAmtronConfigFlow.__init__(self)
        Flow.__init__(self, hass)

    def async_show_progress(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "progress", **kwargs}

    def async_show_progress_done(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "progress_done", **kwargs}


async def _drive_search(flow: SearchFlow, user_input: dict[str, Any]) -> Any:
    """Run the search the way Home Assistant drives a progress step."""

    result = await flow.async_step_user(user_input)
    assert result["type"] == "progress"
    assert result["progress_action"] == "searching"
    assert int(result["description_placeholders"]["candidates"]) > 0
    await asyncio.sleep(0)
    while True:
        result = await flow.async_step_search()
        if result["type"] != "progress":
            break
        await asyncio.sleep(0)
    assert result["type"] == "progress_done"
    return await flow.async_step_search_result()


def test_a_failed_test_searches_the_bus_and_offers_what_it_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, AmtronConnectionError("wrong address"))
        monkeypatch.setattr(
            config_flow,
            "async_search",
            _fake_search(
                SearchResult(
                    config=SerialConfig(
                        port="/dev/fake", baudrate=19200, device_id=23
                    ),
                    identity=IDENTITY,
                    attempts=14,
                )
            ),
        )
        flow = SearchFlow(FakeHass())
        result = await _drive_search(flow, {**USER_INPUT, CONF_SEARCH: "addresses"})

        assert result["step_id"] == "search_result"
        placeholders = result["description_placeholders"]
        assert placeholders["device_id"] == "23"
        assert placeholders["baudrate"] == "19200"
        assert placeholders["attempts"] == "14"
        assert placeholders["serial_number"] == "ABC123456789"

        created = await flow.async_step_search_result({})
        assert created["type"] == "create_entry"
        assert created["data"][CONF_DEVICE_ID] == 23
        assert created["data"][CONF_BAUDRATE] == 19200
        assert created["data"][CONF_STOPBITS] == 2
        assert CONF_SEARCH not in created["data"]
        assert CONF_FRAME not in created["data"]

    asyncio.run(run())


def test_an_empty_bus_comes_back_to_the_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, AmtronConnectionError("nothing"))
        monkeypatch.setattr(config_flow, "async_search", _fake_search(None))
        flow = SearchFlow(FakeHass())
        result = await _drive_search(flow, {**USER_INPUT, CONF_SEARCH: "full"})

        assert result["type"] == "form"
        assert result["errors"] == {"base": "not_found"}

    asyncio.run(run())


def test_an_unusable_port_comes_back_to_the_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, AmtronConnectionError("no port"))

        async def failing(*_args: Any, **_kwargs: Any) -> None:
            raise AmtronConnectionError("cannot open /dev/fake")

        monkeypatch.setattr(config_flow, "async_search", failing)
        flow = SearchFlow(FakeHass())
        result = await _drive_search(flow, {**USER_INPUT, CONF_SEARCH: "addresses"})

        assert result["errors"] == {"base": "not_found"}

    asyncio.run(run())


def test_searching_is_skipped_when_it_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        _patch_connection(monkeypatch, AmtronConnectionError("no port"))
        flow = SearchFlow(FakeHass())
        result = await flow.async_step_user({**USER_INPUT, CONF_SEARCH: "off"})
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}

    asyncio.run(run())


def _fake_search(result: SearchResult | None) -> Any:
    async def search(*_args: Any, **_kwargs: Any) -> SearchResult | None:
        await asyncio.sleep(0)
        return result

    return search
